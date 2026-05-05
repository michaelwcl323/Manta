use config::Committee;
use crypto::{Digest, PublicKey};
use log::{debug, info, warn};
use primary::{Header, Round};
use std::cmp::max;
use std::collections::{HashMap, HashSet};
use tokio::sync::mpsc::{Receiver, Sender};

#[cfg(test)]
#[path = "tests/consensus_tests.rs"]
pub mod consensus_tests;

/// The representation of the DAG in memory.
type DagEntry = Header;
type Dag = HashMap<Round, HashMap<PublicKey, Vec<DagEntry>>>;
type DagPosition = (Round, PublicKey, usize);
const LEADER_NUM: usize = 3;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum HeaderStatus {
    Committed,
    Pending,
    Skipped
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct HeaderState {
    status: HeaderStatus,
}

/// The state that needs to be persisted for crash-recovery.
struct State {
    /// The highest round among all committed headers. This is used for GC only.
    last_committed_header_round: Round,
    /// The round of the last leader whose commit path was accepted.
    last_committed_leader_round: Round,
    /// Keeps the last actually committed round for each authority. This is distinct from the
    /// per-header status map: `HeaderStatus::Committed` means a leader digest was selected and
    /// should not be skipped, while this map is used to avoid sequencing old headers again.
    last_committed_round: HashMap<PublicKey, Round>,
    /// Keeps the latest committed header (and its parents) for every authority. Anything older
    /// must be regularly cleaned up through the function `update`.
    dag: Dag,
    /// Fast lookup for header digests. Used by logging / visualization.
    digest_index: HashMap<Digest, DagPosition>,
    /// Per-header consensus state keyed by the header digest.
    header_states: HashMap<Digest, HeaderState>,
}

impl State {
    fn new(genesis: Vec<Header>) -> Self {
        let mut state = Self {
            last_committed_header_round: 0,
            last_committed_leader_round: 0,
            last_committed_round: HashMap::new(),
            dag: HashMap::new(),
            digest_index: HashMap::new(),
            header_states: HashMap::new(),
        };

        for header in genesis {
            state.insert(header);
        }

        state.last_committed_round = state
            .dag
            .get(&0)
            .into_iter()
            .flat_map(|genesis_round| genesis_round.iter())
            .flat_map(|(author, headers)| headers.iter().map(move |header| (*author, header.round)))
            .collect();
        state
    }

    fn insert(&mut self, header: Header) {
        let round = header.round;
        let origin = header.author;
        let header_id = header.id.clone();

        let headers = self
            .dag
            .entry(round)
            .or_insert_with(HashMap::new)
            .entry(origin)
            .or_insert_with(Vec::new);
        let index = headers.len();
        headers.push(header);

        let position = (round, origin, index);
        self.digest_index.insert(header_id.clone(), position);
        self.header_states.insert(
            header_id,
            HeaderState {
                status: HeaderStatus::Pending,
            },
        );
    }

    fn remove_indexes(&mut self, header_id: &Digest) {
        self.digest_index.remove(header_id);
        self.header_states.remove(header_id);
    }

    fn find_header(&self, header_digest: &Digest) -> Option<&DagEntry> {
        let (round, author, index) = self.digest_index.get(header_digest)?;
        self.dag.get(round)?.get(author)?.get(*index)
    }

    fn find_digest(&self, digest: &Digest) -> Option<DagPosition> {
        self.digest_index.get(digest).copied()
    }

    fn find_header_state(&self, digest: &Digest) -> Option<&HeaderState> {
        self.header_states.get(digest)
    }

    fn set_header_status(&mut self, digest: &Digest, status: HeaderStatus) -> bool {
        match self.header_states.get_mut(digest) {
            Some(header_state) => {
                header_state.status = status;
                true
            }
            None => false,
        }
    }

    /// Update and clean up internal state base on committed headers.
    fn update(&mut self, header: &Header, gc_depth: Round) {
        self.last_committed_round
            .entry(header.author)
            .and_modify(|r| *r = max(*r, header.round))
            .or_insert_with(|| header.round);

        let last_committed_header_round = *self.last_committed_round.values().max().unwrap();
        self.last_committed_header_round = last_committed_header_round;
        let last_committed_round = &self.last_committed_round;
        let mut removed = Vec::new();

        self.dag.retain(|round, authorities| {
            let keep_round = *round + gc_depth >= last_committed_header_round;

            authorities.retain(|author, headers| {
                headers.retain(|header| {
                    let keep_header =
                        keep_round && *round >= last_committed_round.get(author).copied().unwrap_or_default();
                    if !keep_header {
                        removed.push(header.id.clone());
                    }
                    keep_header
                });
                !headers.is_empty()
            });

            !authorities.is_empty()
        });

        for header_id in removed {
            self.remove_indexes(&header_id);
        }
    }

    fn update_last_committed_leader(&mut self, leader_round: Round) {
        self.last_committed_leader_round = max(self.last_committed_leader_round, leader_round);
    }
}

pub struct Consensus {
    /// The committee information.
    committee: Committee,
    /// Authorities in deterministic order for leader election.
    authorities: Vec<PublicKey>,
    /// Cache of authority -> node id used by logs and visualization.
    author_to_node: HashMap<PublicKey, usize>,
    /// The depth of the garbage collector.
    gc_depth: Round,

    /// Receives new headers from the primary. The primary should send us new headers only
    /// if it already sent us its whole history.
    rx_primary: Receiver<Header>,
    /// Outputs the sequence of ordered headers to the primary (for cleanup and feedback).
    tx_primary: Sender<Header>,
    /// Outputs the sequence of ordered headers to the application layer.
    tx_output: Sender<Header>,

    /// The genesis headers.
    genesis: Vec<Header>,
}

impl Consensus {
    pub fn spawn(
        committee: Committee,
        gc_depth: Round,
        rx_primary: Receiver<Header>,
        tx_primary: Sender<Header>,
        tx_output: Sender<Header>,
    ) {
        let authorities: Vec<_> = committee.authorities.keys().copied().collect();
        let author_to_node = authorities
            .iter()
            .copied()
            .enumerate()
            .map(|(index, authority)| (authority, index))
            .collect();
        tokio::spawn(async move {
            Self {
                committee: committee.clone(),
                authorities,
                author_to_node,
                gc_depth,
                rx_primary,
                tx_primary,
                tx_output,
                genesis: Header::genesis(&committee),
            }
            .run()
            .await;
        });
    }

    async fn run(&mut self) {
        // The consensus state (everything else is immutable).
        let mut state = State::new(self.genesis.clone());

        // Listen to incoming headers.
        while let Some(header) = self.rx_primary.recv().await {
            debug!("Processing {:?}", header);
            let round = header.round();

            // Add the new header to the local storage.
            state.insert(header);

            // DAG visualization is extremely verbose; keep it disabled unless
            // explicitly requested for debugging / offline extraction.
            if std::env::var("MAHI_LOG_DAG").ok().as_deref() == Some("1") {
                self.visualize_dag(&state, round);
            }

            let wave_length = self.committee.solid_wave_length();
            let r = round - 1;
            if r % wave_length != 0 {
                continue;
            }
            if r < 2 * wave_length {
                continue;
            }
            let leader_round = r - wave_length;
            if leader_round <= state.last_committed_leader_round {
                debug!(
                    "Skipping leader_round {} because last_committed_leader_round={}",
                    leader_round, state.last_committed_leader_round
                );
                continue;
            }

            let (leaders, fully_resolved) = self.leaders(leader_round, &mut state);
            if leaders.is_empty() {
                debug!("No leaders in DAG for leader_round {}", leader_round);
                continue;
            }
            debug!(
                "Leader candidates for round {}: authority_count={}, candidate_count={}",
                leader_round,
                LEADER_NUM.min(self.authorities.len()),
                leaders.len()
            );

            if !fully_resolved {
                continue;
            }

            let mut sequence = Vec::new();
            let mut sequenced = HashSet::new();
            for leader in leaders.iter().rev() {
                for ordered_leader in self.order_leaders(leader, &mut state).iter().rev() {
                    for committed in self.order_dag(ordered_leader, &state) {
                        if sequenced.insert(committed.id.clone()) {
                            state.update(&committed, self.gc_depth);
                            sequence.push(committed);
                        }
                    }
                }
            }

            if sequence.is_empty() {
                continue;
            }

            state.update_last_committed_leader(leader_round);

            for header in sequence {
                let node_id = self.author_to_node_id(header.author);
                info!(
                    "DAG_COMMITTED round={} node={} digest={:?}",
                    header.round,
                    node_id,
                    header.id
                );
                #[cfg(not(feature = "benchmark"))]
                info!("Committed {}", header);

                #[cfg(feature = "benchmark")]
                for digest in header.payload.keys() {
                    info!("Committed {} -> {:?}", header, digest);
                }

                self.tx_primary
                    .send(header.clone())
                    .await
                    .expect("Failed to send header to primary");

                if let Err(e) = self.tx_output.send(header).await {
                    warn!("Failed to output header: {}", e);
                }
            }


//             // Narwhal-style commit loop adapted to solid waves:
//             // only commit on solid-wave boundary rounds, and validate the leader from the
//             // previous solid wave using the previous solid-step round as support.
//             let step_length = self.committee.solid_step_length();
//             let wave_length = self.committee.solid_wave_length();
//             let r = round - step_length;
//             let leader_round = r - wave_length;
//             let support_round = r - step_length;
//             if r % wave_length != 0 {
//                 continue;
//             }
//             if r < 2 * wave_length {
//                 continue;
//             }
//             if leader_round <= state.last_committed_leader_round {
//                 debug!(
//                     "Skipping leader_round {} because last_committed_leader_round={}",
//                     leader_round, state.last_committed_leader_round
//                 );
//                 continue;
//             }

//             let (leader_digest, leader) = match self.leader(leader_round, &state.dag) {
//                 Some((digest, cert)) => (digest.clone(), cert.clone()),
//                 None => {
//                     debug!(
//                         "No leader in DAG for leader_round {} (support_round={})",
//                         leader_round, support_round
//                     );
//                     continue;
//                 }
//             };

//             // `leader_digest` is the *certificate digest* returned by `State::dag[leader_round][leader]`.
//             // `leader.header.id` is the leader block's *header id*.
//             //
//             // As in Narwhal, a single support check decides whether we can commit this leader.
//             // The Manta-specific part is the support basis: rather than direct parent edges, we
//             // use `solid_wave_vertices` from the support round.
//             let leader_header_id = leader.header.id.clone();
//             if log_enabled!(log::Level::Debug) {
//                 let header_pos = self.find_certificate_in_dag(&state, &leader_header_id);
//                 let cert_pos = self.find_certificate_in_dag(&state, &leader_digest);

//                 debug!(
//                     "Commit validity check: round={}, leader_round={}, support_round={}. \
// leader_header_id={:?} -> {:?} (node_id={}); \
// leader_digest(cert)= {:?} -> {:?} (node_id={})",
//                     round,
//                     leader_round,
//                     support_round,
//                     leader_header_id,
//                     header_pos.as_ref().map(|(rd, _)| rd),
//                     header_pos
//                         .map(|(_, a)| self.author_to_node_id(a))
//                         .unwrap_or(999),
//                     leader_digest,
//                     cert_pos.as_ref().map(|(rd, _)| rd),
//                     cert_pos
//                         .map(|(_, a)| self.author_to_node_id(a))
//                         .unwrap_or(999),
//                 );
//             }
//             let support_round_map = state
//                 .dag
//                 .get(&support_round)
//                 .expect("Support round should exist in the local DAG");
//             let debug_logging = log_enabled!(log::Level::Debug);
//             let mut support_nodes = Vec::new();
//             let mut support_entries = if debug_logging {
//                 Some(Vec::with_capacity(support_round_map.len()))
//             } else {
//                 None
//             };
//             let mut stake = 0;
//             for (_, certificate) in support_round_map.values() {
//                 let vertices = &certificate.header.solid_wave_vertices;
//                 let supports =
//                     vertices.contains(&leader_header_id) || vertices.contains(&leader_digest);
//                 let node_id = self.author_to_node_id(certificate.origin());

//                 if supports {
//                     support_nodes.push(node_id);
//                     stake += self.committee.stake(&certificate.origin());
//                 }

//                 if let Some(entries) = support_entries.as_mut() {
//                     entries.push(format!(
//                         "[{},{}]:support={} solid=[{}] merged=[{}]",
//                         certificate.round(),
//                         node_id,
//                         supports,
//                         self.render_digest_set(&state, &certificate.header.solid_wave_vertices),
//                         self.render_digest_set(
//                             &state,
//                             &certificate.header.solid_wave_vertices_merged
//                         ),
//                     ));
//                 }
//             }
//             support_nodes.sort_unstable();
//             let threshold = self.committee.validity_threshold();
//             let leader_node = self.author_to_node_id(leader.origin());
//             if stake < threshold {
//                 info!(
//                     "DAG_COMMIT_CHECK path=solid leader_round={} leader_node={} support_round={} support_basis=solid_wave_vertices stake={} threshold={} result=insufficient_stake support_set={:?}",
//                     leader_round,
//                     leader_node,
//                     support_round,
//                     stake,
//                     threshold,
//                     support_nodes
//                 );
//                 if log_enabled!(log::Level::Debug) && stake == 0 {
//                     debug!(
//                         "Validity stake=0 detail: leader_round={}, support_round={}, leader_header_id={:?}, leader_digest(cert)={:?}",
//                         leader_round, support_round, leader_header_id, leader_digest
//                     );

//                     if let Some(round_map) = state.dag.get(&support_round) {
//                         let mut certs: Vec<_> = round_map.values().collect();
//                         certs.sort_by_key(|(_, cert)| self.author_to_node_id(cert.origin()));

//                         for (cert_digest, cert) in certs {
//                             let origin = cert.origin();
//                             let node_id = self.author_to_node_id(origin);

//                             let base = &cert.header.solid_wave_vertices;
//                             let vertices = base;

//                             let contains_leader_header = vertices.contains(&leader_header_id);
//                             let contains_leader_digest = vertices.contains(&leader_digest);

//                             let mut resolved: Vec<String> = Vec::with_capacity(vertices.len());
//                             for d in vertices.iter() {
//                                 if let Some((rd, a)) = self.find_certificate_in_dag(&state, d) {
//                                     let nid = self.author_to_node_id(a);
//                                     resolved.push(format!("[{},{}]", rd, nid));
//                                 } else {
//                                     resolved.push("[?,?]".to_string());
//                                 }
//                             }
//                             resolved.sort();

//                             debug!(
//                                 "support_round cert: node={} cert_round={} cert_digest={:?} base_len={} contains(leader_header_id)={} contains(leader_digest)={} vertices={}",
//                                 node_id,
//                                 cert.round(),
//                                 cert_digest,
//                                 base.len(),
//                                 contains_leader_header,
//                                 contains_leader_digest,
//                                 resolved.join(", ")
//                             );
//                         }
//                     } else {
//                         debug!(
//                             "Validity stake=0 detail: support_round {} missing from local DAG",
//                             support_round
//                         );
//                     }
//                 }
//                 debug!(
//                     "Current stake is {}. Leader {:?} does not have enough support",
//                     stake, leader
//                 );
//                 if let Some(entries) = support_entries {
//                     debug!(
//                         "DAG_COMMIT_SUPPORT leader_round={} support_round={} detail={}",
//                         leader_round,
//                         support_round,
//                         entries.join(" | ")
//                     );
//                 }
//                 continue;
//             }

//             info!(
//                 "DAG_COMMIT_CHECK path=solid leader_round={} leader_node={} support_round={} support_basis=solid_wave_vertices stake={} threshold={} result=committed support_set={:?}",
//                 leader_round,
//                 leader_node,
//                 support_round,
//                 stake,
//                 threshold,
//                 support_nodes
//             );
//             if let Some(entries) = support_entries {
//                 debug!(
//                     "DAG_COMMIT_SUPPORT leader_round={} support_round={} detail={}",
//                     leader_round,
//                     support_round,
//                     entries.join(" | ")
//                 );
//             }

//             debug!("Leader {:?} has enough support", leader);
//             let mut sequence = Vec::new();
//             for leader in self.order_leaders(&leader, &state).iter().rev() {
//                 for x in self.order_dag(leader, &state) {
//                     state.update(&x, self.gc_depth);
//                     sequence.push(x);
//                 }
//             }
//             state.update_last_committed_leader(leader_round);

//             if log_enabled!(log::Level::Debug) {
//                 for (name, round) in &state.last_committed {
//                     debug!("Latest commit of {}: Round {}", name, round);
//                 }
//             }

//             for certificate in sequence {
//                 let node_id = self.author_to_node_id(certificate.origin());
//                 info!(
//                     "DAG_COMMITTED round={} node={} digest={:?}",
//                     certificate.round(),
//                     node_id,
//                     certificate.digest()
//                 );
//                 #[cfg(not(feature = "benchmark"))]
//                 info!("Committed {}", certificate.header);

//                 #[cfg(feature = "benchmark")]
//                 for digest in certificate.header.payload.keys() {
//                     info!("Committed {} -> {:?}", certificate.header, digest);
//                 }

//                 self.tx_primary
//                     .send(certificate.clone())
//                     .await
//                     .expect("Failed to send certificate to primary");

//                 if let Err(e) = self.tx_output.send(certificate).await {
//                     warn!("Failed to output certificate: {}", e);
//                 }
//             }
        }
    }

    /// Map authority public key to node id (0..n-1), same as visualize_dag / extract_dag_out.
    fn author_to_node_id(&self, author: PublicKey) -> usize {
        self.author_to_node.get(&author).copied().unwrap_or(999)
    }

    fn leaders(&self, round: Round, state: &mut State) -> (Vec<Header>, bool) {
        let Some(round_map) = state.dag.get(&round) else {
            return (Vec::new(), false);
        };

        let authority_count = self.authorities.len();
        if authority_count == 0 {
            return (Vec::new(), false);
        }

        #[cfg(test)]
        let round_robin = 0;
        #[cfg(not(test))]
        let round_robin = round;

        let base = round_robin as usize % authority_count;
        let leader_count = LEADER_NUM.min(authority_count);
        let support_round = round + 2;
        let threshold = self.committee.quorum_threshold();
        let can_resolve = state.dag.contains_key(&support_round);

        let mut grouped_digests = Vec::new();
        for offset in 0..leader_count {
            let index = (base + offset) % authority_count;
            let leader_authority = self.authorities[index];
            if let Some(headers) = round_map.get(&leader_authority) {
                let mut digests: Vec<_> = headers.iter().map(|header| header.id.clone()).collect();
                digests.sort();
                grouped_digests.push((leader_authority, digests));
            }
        }

        let mut fully_resolved = can_resolve;
        let mut selected_digests = Vec::new();
        for (author, digests) in grouped_digests {
            let existing_committed = digests.iter().find_map(|digest| {
                matches!(
                    state.find_header_state(digest).map(|header_state| header_state.status),
                    Some(HeaderStatus::Committed)
                )
                .then(|| digest.clone())
            });

            if let Some(committed_digest) = existing_committed {
                for digest in &digests {
                    if digest != &committed_digest {
                        state.set_header_status(digest, HeaderStatus::Skipped);
                    }
                }
                selected_digests.push(committed_digest);
                continue;
            }

            if !can_resolve {
                fully_resolved = false;
                selected_digests.extend(
                    digests
                        .into_iter()
                        .filter(|digest| {
                            !matches!(
                                state.find_header_state(digest).map(|header_state| header_state.status),
                                Some(HeaderStatus::Skipped)
                            )
                        }),
                );
                continue;
            }

            let mut committed_digest = None;
            for digest in &digests {
                if matches!(
                    state.find_header_state(digest).map(|header_state| header_state.status),
                    Some(HeaderStatus::Skipped)
                ) {
                    continue;
                }

                let support = self.support_stake_for_digest(state, support_round, digest);
                debug!(
                    "Leader support check: leader_round={} support_round={} author={} digest={:?} stake={} threshold={}",
                    round,
                    support_round,
                    author,
                    digest,
                    support,
                    threshold
                );
                if support >= threshold {
                    committed_digest = Some(digest.clone());
                    break;
                }
            }

            if let Some(committed_digest) = committed_digest {
                state.set_header_status(&committed_digest, HeaderStatus::Committed);
                info!(
                    "Leader committed: round={} author={} digest={:?}",
                    round,
                    author,
                    committed_digest
                );
                for digest in &digests {
                    if digest != &committed_digest {
                        state.set_header_status(digest, HeaderStatus::Skipped);
                        info!(
                            "Leader skipped: round={} author={} digest={:?}",
                            round,
                            author,
                            digest
                        );
                    }
                }
                selected_digests.push(committed_digest);
            } else {
                fully_resolved = false;
                selected_digests.extend(
                    digests
                        .into_iter()
                        .filter(|digest| {
                            !matches!(
                                state.find_header_state(digest).map(|header_state| header_state.status),
                                Some(HeaderStatus::Skipped)
                            )
                        }),
                );
            }
        }

        let leaders = selected_digests
            .into_iter()
            .filter_map(|digest| state.find_header(&digest).cloned())
            .collect();
        (leaders, fully_resolved)
    }

    fn support_stake_for_digest(&self, state: &State, support_round: Round, digest: &Digest) -> u32 {
        let Some(round_map) = state.dag.get(&support_round) else {
            return 0;
        };

        round_map.iter().fold(0, |stake, (author, headers)| {
            if headers
                .iter()
                .any(|header| header.solid_wave_vertices.contains(digest))
            {
                stake + self.committee.stake(author)
            } else {
                stake
            }
        })
    }

    fn order_leaders(&self, leader: &Header, state: &mut State) -> Vec<Header> {
        let wave = self.committee.solid_wave_length() as usize;
        if wave == 0 {
            return vec![leader.clone()];
        }

        let start = state
            .last_committed_leader_round
            .saturating_add(wave as u64);
        let end_round = leader.round;
        let mut to_commit = vec![leader.clone()];
        let mut frontier = vec![leader.clone()];

        for r in (start..end_round).rev().step_by(wave) {
            let (previous_leaders, _) = self.leaders(r, state);
            if previous_leaders.is_empty() {
                continue;
            }

            let mut next_frontier = Vec::new();
            for previous_leader in previous_leaders {
                if frontier
                    .iter()
                    .any(|current| self.linked(current, &previous_leader, state))
                {
                    to_commit.push(previous_leader.clone());
                    next_frontier.push(previous_leader);
                }
            }

            if !next_frontier.is_empty() {
                frontier = next_frontier;
            }
        }

        debug!(
            "order_leaders: chain_len={} tip_round={} last_committed_leader_round={} gap_rounds={} step={}",
            to_commit.len(),
            end_round,
            state.last_committed_leader_round,
            end_round.saturating_sub(state.last_committed_leader_round),
            wave
        );
        to_commit
    }

    fn find_parent_header<'a>(
        &self,
        state: &'a State,
        child_round: Round,
        parent_digest: &Digest,
    ) -> Option<&'a Header> {
        if child_round <= 1 {
            return None;
        }

        let parent = state
            .find_header(parent_digest)
            .filter(|header| header.round < child_round)?;

        if matches!(
            state
                .find_header_state(&parent.id)
                .map(|header_state| header_state.status),
            Some(HeaderStatus::Skipped)
        ) {
            return None;
        }

        if state
            .last_committed_round
            .get(&parent.author)
            .map_or(false, |round| parent.round <= *round)
        {
            return None;
        }

        Some(parent)
    }

    fn linked(&self, leader: &Header, prev_leader: &Header, state: &State) -> bool {
        let target = prev_leader.id.clone();
        let mut stack = vec![leader];
        let mut visited = HashSet::new();

        while let Some(current) = stack.pop() {
            let current_digest = current.id.clone();
            if !visited.insert(current_digest.clone()) {
                continue;
            }
            if current_digest == target {
                return true;
            }

            for parent in &current.parents {
                if let Some(parent_header) = self.find_parent_header(state, current.round, parent) {
                    stack.push(parent_header);
                }
            }
        }
        false
    }

    fn order_dag(&self, leader: &Header, state: &State) -> Vec<Header> {
        debug!("Processing sub-dag of {:?}", leader);
        let mut ordered = Vec::new();
        let mut already_ordered: HashSet<Digest> = HashSet::new();
        let mut buffer = vec![leader];

        while let Some(current) = buffer.pop() {
            if matches!(
                state
                    .find_header_state(&current.id)
                    .map(|header_state| header_state.status),
                Some(HeaderStatus::Skipped)
            ) {
                continue;
            }

            if state
                .last_committed_round
                .get(&current.author)
                .map_or(false, |round| current.round <= *round)
            {
                continue;
            }

            debug!("Sequencing {:?}", current);
            ordered.push(current.clone());
            for parent in &current.parents {
                let header = match self.find_parent_header(state, current.round, parent) {
                    Some(header) => header,
                    None => continue,
                };

                let mut skip = already_ordered.contains(&header.id);
                skip |= state
                    .last_committed_round
                    .get(&header.author)
                    .map_or(false, |round| header.round <= *round);
                if !skip {
                    buffer.push(header);
                    already_ordered.insert(header.id.clone());
                }
            }
        }

        ordered.retain(|header| header.round + self.gc_depth >= state.last_committed_header_round);
        ordered.sort_by_key(|header| header.round);
        ordered
    }

    // /// Order leader headers to commit, mirroring Narwhal's `order_leaders` with step
    // /// `solid_wave_length`: walk rounds from `last_committed_leader_round + solid_wave_length`
    // /// up to (exclusive) the current leader round, stepping backward by `solid_wave_length`, and
    // /// chain predecessors via `linked`.
    // fn order_leaders(&self, leader: &Header, state: &State) -> Vec<Header> {
    //     let wave = self.committee.solid_wave_length() as usize;
    //     if wave == 0 {
    //         return vec![leader.clone()];
    //     }
    //     let start = state
    //         .last_committed_leader_round
    //         .saturating_add(wave as u64);
    //     let end_round = leader.round;
    //     let mut to_commit = vec![leader.clone()];
    //     let mut cur = leader;
    //     for r in (start..end_round).rev().step_by(wave) {
    //         let prev_leader = match self.leader(r, &state.dag) {
    //             Some(x) => x,
    //             None => continue,
    //         };
    //         if self.linked(cur, prev_leader, state) {
    //             to_commit.push(prev_leader.clone());
    //             cur = prev_leader;
    //         }
    //     }
    //     debug!(
    //         "order_leaders: chain_len={} tip_round={} last_committed_leader_round={} gap_rounds={} step={}",
    //         to_commit.len(),
    //         end_round,
    //         state.last_committed_leader_round,
    //         end_round.saturating_sub(state.last_committed_leader_round),
    //         wave
    //     );
    //     to_commit
    // }

    // /// Find a parent header by digest in any ancestor round (< child_round).
    // fn find_parent_header<'a>(
    //     &self,
    //     state: &'a State,
    //     child_round: Round,
    //     parent_digest: &Digest,
    // ) -> Option<&'a Header> {
    //     if child_round <= 1 {
    //         return None;
    //     }
    //     state
    //         .find_header(parent_digest)
    //         .filter(|header| header.round < child_round)
    // }

    // /// Checks if there is a path between two leaders.
    // /// Unlike the original implementation, this traversal follows weak edges too.
    // fn linked(&self, leader: &Header, prev_leader: &Header, state: &State) -> bool {
    //     let target = prev_leader.id.clone();
    //     let mut stack = vec![leader];
    //     let mut visited = HashSet::new();

    //     while let Some(current) = stack.pop() {
    //         let current_digest = current.id.clone();
    //         if !visited.insert(current_digest.clone()) {
    //             continue;
    //         }
    //         if current_digest == target {
    //             return true;
    //         }

    //         for parent in &current.parents {
    //             if let Some(parent_header) = self.find_parent_header(state, current.round, parent) {
    //                 stack.push(parent_header);
    //             }
    //         }
    //     }
    //     false
    // }

    // /// Flatten the dag referenced by the input header. This is a classic depth-first search (pre-order):
    // /// https://en.wikipedia.org/wiki/Tree_traversal#Pre-order
    // fn order_dag(&self, leader: &Header, state: &State) -> Vec<Header> {
    //     debug!("Processing sub-dag of {:?}", leader);
    //     let mut ordered = Vec::new();
    //     let mut already_ordered: HashSet<Digest> = HashSet::new();

    //     let mut buffer = vec![leader];
    //     while let Some(x) = buffer.pop() {
    //         debug!("Sequencing {:?}", x);
    //         ordered.push(x.clone());
    //         for parent in &x.parents {
    //             let header = match self.find_parent_header(state, x.round, parent) {
    //                     Some(x) => x,
    //                     None => continue, // Parent already GC'ed or not in local DAG.
    //                 };

    //             // We skip the header if we (1) already processed it or (2) we reached a round that we already
    //             // committed for this authority.
    //             let mut skip = already_ordered.contains(&header.id);
    //             skip |= state
    //                 .last_committed
    //                 .get(&header.author)
    //                 .map_or_else(|| false, |r| r == &header.round);
    //             if !skip {
    //                 buffer.push(header);
    //                 already_ordered.insert(header.id.clone());
    //             }
    //         }
    //     }

    //     // Ensure we do not commit garbage collected headers.
    //     ordered.retain(|x| x.round + self.gc_depth >= state.last_committed_header_round);

    //     // Ordering the output by round is not really necessary but it makes the commit sequence prettier.
    //     ordered.sort_by_key(|x| x.round);
    //     ordered
    // }

    fn visualize_dag(&self, state: &State, current_round: Round) {
        // from current_round to round 1, reverse
        for round in (1..=current_round).rev() {
            if state.dag.contains_key(&round) {
                let round_headers = state.dag.get(&round).unwrap();
                let mut round_output = format!("Round {}:", round);
                let mut vertices = Vec::new();

                let mut sorted_headers: Vec<_> = round_headers.iter().collect();
                sorted_headers.sort_by_key(|(author, _)| *author);

                for (author, headers) in sorted_headers {
                    let node_id = self.author_to_node.get(author).unwrap_or(&999);

                    for (index, header) in headers.iter().enumerate() {
                        let vertex_name = if headers.len() == 1 {
                            format!("Vertex{}", node_id)
                        } else {
                            format!("Vertex{}#{}", node_id, index)
                        };

                        // find the parent nodes
                        let mut parents = Vec::new();
                        for parent_digest in &header.parents {
                            // find the parent header in the dag
                            if let Some((parent_round, parent_author)) =
                                self.find_certificate_in_dag(state, parent_digest)
                            {
                                if parent_round + 1 != round {
                                    continue;
                                }
                                let parent_node_id =
                                    self.author_to_node.get(&parent_author).unwrap_or(&999);
                                parents.push(format!("[{},{}]", parent_round, parent_node_id));
                            } else {
                                // if the block is genesis, do not need to output
                                if round != 1 {
                                    parents.push("[?,?]".to_string());
                                }
                            }
                        }

                        let parent_str = if parents.is_empty() {
                            "[]".to_string()
                        } else {
                            format!("[{}]", parents.join(", "))
                        };

                        // Resolve each solid_wave_vertex digest to [round, node_id] for explicit display.
                        let mut solid_vertices = Vec::new();
                        for digest in &header.solid_wave_vertices {
                            if let Some((r, author)) = self.find_certificate_in_dag(state, digest) {
                                let n = self.author_to_node.get(&author).unwrap_or(&999);
                                solid_vertices.push(format!("[{},{}]", r, n));
                            } else {
                                solid_vertices.push("[?,?]".to_string());
                            }
                        }
                        let solid_str = if solid_vertices.is_empty() {
                            "".to_string()
                        } else {
                            format!(" solid=[{}]", solid_vertices.join(", "))
                        };

                        // Resolve each merged solid_wave_vertex digest to [round, node_id].
                        let mut merged_vertices = Vec::new();
                        for digest in &header.solid_wave_vertices_merged {
                            if let Some((r, author)) = self.find_certificate_in_dag(state, digest) {
                                let n = self.author_to_node.get(&author).unwrap_or(&999);
                                merged_vertices.push(format!("[{},{}]", r, n));
                            } else {
                                merged_vertices.push("[?,?]".to_string());
                            }
                        }
                        let merged_str = if merged_vertices.is_empty() {
                            "".to_string()
                        } else {
                            format!(" merged=[{}]", merged_vertices.join(", "))
                        };

                        let vertex_str = format!(
                            "({}){} (solid_wave_vertices: {}){}{}",
                            vertex_name,
                            parent_str,
                            header.solid_wave_vertices.len(),
                            solid_str,
                            merged_str
                        );
                        vertices.push(vertex_str);
                    }
                }

                if !vertices.is_empty() {
                    round_output.push_str(&format!(" {} ", vertices.join(" --- ")));
                    info!("{}", round_output);
                }
            }
        }
    }

    fn find_certificate_in_dag(
        &self,
        state: &State,
        digest: &Digest,
    ) -> Option<(Round, PublicKey)> {
        state.find_digest(digest).map(|(round, author, _)| (round, author))
    }

    fn render_digest_set(&self, state: &State, digests: &HashSet<Digest>) -> String {
        let mut resolved = Vec::with_capacity(digests.len());
        for digest in digests {
            if let Some((round, author)) = self.find_certificate_in_dag(state, digest) {
                resolved.push(format!("[{},{}]", round, self.author_to_node_id(author)));
            } else {
                resolved.push("[?,?]".to_string());
            }
        }
        resolved.sort();
        resolved.join(", ")
    }
}

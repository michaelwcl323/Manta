use config::{Committee, Stake};
use crypto::Hash as _;
use crypto::{Digest, PublicKey};
use log::{debug, info, warn};
use primary::{Certificate, Round};
use std::cmp::max;
use std::collections::{HashMap, HashSet};
use tokio::sync::mpsc::{Receiver, Sender};

#[cfg(test)]
#[path = "tests/consensus_tests.rs"]
pub mod consensus_tests;

/// The representation of the DAG in memory.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CommitStatus {
    OneValent = 0,
    ZeroValent = 1,
    Bivalent = 2,
    Pending = 3,
}

type DagEntry = (Digest, Certificate, CommitStatus);
type Dag = HashMap<Round, HashMap<PublicKey, DagEntry>>;
type DagPosition = (Round, PublicKey);

/// The state that needs to be persisted for crash-recovery.
struct State {
    /// The highest round among all committed certificates. This is used for GC only.
    last_committed_round: Round,
    /// The round of the last leader whose commit path was accepted.
    last_committed_leader_round: Round,
    /// All certificates that have already been committed (fast or slow).
    committed_digests: HashSet<Digest>,
    /// Certificates committed specifically by slow path.
    slow_path_committed_digests: HashSet<Digest>,
    /// Keeps the latest committed certificate (and its parents) for every authority. Anything older
    /// must be regularly cleaned up through the function `update`.
    dag: Dag,
    /// Fast lookup for parent certificate digests.
    certificate_index: HashMap<Digest, DagPosition>,
    /// Fast lookup for both certificate digests and header ids. Used by logging / visualization.
    digest_index: HashMap<Digest, DagPosition>,
    /// The earliest round that fast-path failed to commit and must be retried by slow-path.
    slow_path_pending_round: Option<Round>,
    /// Rounds that have been buffered by fast path (undecided > 0).
    buffered_rounds: HashSet<Round>,
    /// Rounds already evaluated by fast path (enforce one check per round).
    fast_path_checked_rounds: HashSet<Round>,
}

impl State {
    fn new(genesis: Vec<Certificate>) -> Self {
        let mut state = Self {
            last_committed_round: 0,
            last_committed_leader_round: 0,
            committed_digests: HashSet::new(),
            slow_path_committed_digests: HashSet::new(),
            dag: HashMap::new(),
            certificate_index: HashMap::new(),
            digest_index: HashMap::new(),
            slow_path_pending_round: None,
            buffered_rounds: HashSet::new(),
            fast_path_checked_rounds: HashSet::new(),
        };

        for certificate in genesis {
            state.insert(certificate);
        }

        state.committed_digests = state
            .dag
            .get(&0)
            .into_iter()
            .flat_map(|genesis_round| genesis_round.iter())
            .map(|(_, (digest, _, _))| digest.clone())
            .collect();

        state
    }

    fn insert(&mut self, certificate: Certificate) {
        let round = certificate.round();
        let origin = certificate.origin();
        let certificate_digest = certificate.digest();
        let header_id = certificate.header.id.clone();

        if let Some((old_digest, old_certificate, _old_status)) =
            self.dag.entry(round).or_insert_with(HashMap::new).insert(
                origin,
                (
                    certificate_digest.clone(),
                    certificate,
                    CommitStatus::Pending,
                ),
            )
        {
            self.remove_indexes(&old_digest, &old_certificate.header.id);
        }

        let position = (round, origin);
        self.certificate_index
            .insert(certificate_digest.clone(), position);
        self.digest_index
            .insert(certificate_digest.clone(), position);
        self.digest_index.insert(header_id, position);
    }

    fn remove_indexes(&mut self, certificate_digest: &Digest, header_id: &Digest) {
        self.certificate_index.remove(certificate_digest);
        self.digest_index.remove(certificate_digest);
        self.digest_index.remove(header_id);
    }

    fn find_certificate(&self, certificate_digest: &Digest) -> Option<&DagEntry> {
        let (round, author) = self.certificate_index.get(certificate_digest)?;
        self.dag.get(round)?.get(author)
    }

    fn find_digest(&self, digest: &Digest) -> Option<DagPosition> {
        self.digest_index.get(digest).copied()
    }

    /// Record that a certificate has been committed without cleaning the DAG yet.
    fn record_commit(&mut self, certificate: &Certificate, slow_path: bool) -> bool {
        let digest = certificate.digest();
        if !self.committed_digests.insert(digest.clone()) {
            return false;
        }
        if slow_path {
            self.slow_path_committed_digests.insert(digest);
        }
        self.last_committed_round = max(self.last_committed_round, certificate.round());
        true
    }

    /// Clean up internal DAG state using the rounds recorded as committed.
    fn cleanup_committed_history(&mut self, gc_depth: Round) {
        let last_committed_round = self.last_committed_round;
        let mut removed = Vec::new();

        self.dag.retain(|round, authorities| {
            let keep_round = *round + gc_depth >= last_committed_round;

            authorities.retain(|_author, (digest, certificate, _status)| {
                let keep_certificate = keep_round;
                if !keep_certificate {
                    removed.push((digest.clone(), certificate.header.id.clone()));
                }
                keep_certificate
            });

            !authorities.is_empty()
        });

        for (certificate_digest, header_id) in removed {
            self.remove_indexes(&certificate_digest, &header_id);
        }
    }

    fn set_commit_status(&mut self, certificate: &Certificate, status: CommitStatus) {
        if let Some((_, _, current_status)) = self
            .dag
            .get_mut(&certificate.round())
            .and_then(|round_map| round_map.get_mut(&certificate.origin()))
        {
            *current_status = status;
        }
    }

    fn update_last_committed_leader(&mut self, leader_round: Round) {
        self.last_committed_leader_round = max(self.last_committed_leader_round, leader_round);
    }

    fn note_buffered_round(&mut self, round: Round) {
        self.buffered_rounds.insert(round);
        self.slow_path_pending_round = Some(
            self.slow_path_pending_round
                .map_or(round, |pending| pending.min(round)),
        );
    }

    fn round_has_bivalent(&self, round: Round) -> bool {
        self.dag
            .get(&round)
            .map(|authorities| {
                authorities
                    .values()
                    .any(|(_digest, _certificate, status)| *status == CommitStatus::Bivalent)
            })
            .unwrap_or(false)
    }

    fn refresh_buffered_rounds(&mut self) {
        let rounds: Vec<_> = self.buffered_rounds.iter().copied().collect();
        for round in rounds {
            let unresolved = self
                .dag
                .get(&round)
                .map(|authorities| {
                    authorities.values().any(|(digest, _certificate, status)| {
                        *status != CommitStatus::ZeroValent
                            && !self.committed_digests.contains(digest)
                    })
                })
                .unwrap_or(false);
            if !unresolved {
                self.buffered_rounds.remove(&round);
            }
        }
        self.slow_path_pending_round = self.buffered_rounds.iter().copied().min();
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

    /// Receives new certificates from the primary. The primary should send us new certificates only
    /// if it already sent us its whole history.
    rx_primary: Receiver<Certificate>,
    /// Outputs the sequence of ordered certificates to the primary (for cleanup and feedback).
    tx_primary: Sender<Certificate>,
    /// Outputs the sequence of ordered certificates to the application layer.
    tx_output: Sender<Certificate>,

    /// The genesis certificates.
    genesis: Vec<Certificate>,
}

impl Consensus {
    pub fn spawn(
        committee: Committee,
        gc_depth: Round,
        rx_primary: Receiver<Certificate>,
        tx_primary: Sender<Certificate>,
        tx_output: Sender<Certificate>,
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
                genesis: Certificate::genesis(&committee),
            }
            .run()
            .await;
        });
    }

    async fn run(&mut self) {
        // The consensus state (everything else is immutable).
        let mut state = State::new(self.genesis.clone());

        // Listen to incoming certificates.
        while let Some(certificate) = self.rx_primary.recv().await {
            debug!("Processing {:?}", certificate);
            let round = certificate.round();

            // Add the new certificate to the local storage.
            state.insert(certificate);

            // Fast Path
            self.fast_path(round, &mut state).await;
            // If fast-path could not commit, keep retrying this pending round on slow-path
            // until it eventually becomes committable.
            self.slow_path(round, &mut state).await;
        }
    }

    async fn emit_commits(&self, path: &str, certificates: Vec<Certificate>) {
        for certificate in certificates {
            let node_id = self.author_to_node_id(certificate.origin());
            info!(
                "DAG_COMMITTED path={} round={} node={} digest={:?}",
                path,
                certificate.round(),
                node_id,
                certificate.digest()
            );
            #[cfg(not(feature = "benchmark"))]
            info!("Committed {}", certificate.header);

            #[cfg(feature = "benchmark")]
            for digest in certificate.header.payload.keys() {
                info!("Committed {} -> {:?}", certificate.header, digest);
            }

            self.tx_primary
                .send(certificate.clone())
                .await
                .expect("Failed to send certificate to primary");

            if let Err(e) = self.tx_output.send(certificate).await {
                warn!("Failed to output certificate: {}", e);
            }
        }
    }

    async fn fast_path(&self, round: Round, state: &mut State) -> bool {
        if round == 0 {
            return false;
        }
        let target_round = round.saturating_sub(1);
        let allow_retry = state.fast_path_checked_rounds.contains(&round)
            && state.round_has_bivalent(target_round);
        if state.fast_path_checked_rounds.contains(&round) && !allow_retry {
            return false;
        }
        let Some(current_round_map) = state.dag.get(&round) else {
            return false;
        };
        let current_round_certificates: Vec<Certificate> = current_round_map
            .values()
            .map(|(_, cert, _)| cert.clone())
            .collect();

        let current_round_stake: Stake = current_round_certificates
            .iter()
            .map(|x| self.committee.stake(&x.origin()))
            .sum();
        if round > 1 && current_round_stake < self.committee.quorum_threshold() {
            return false;
        }

        let r = round - 1;
        let Some(previous_round_map) = state.dag.get(&r) else {
            debug!(
                "Skipping fast path for round {} because round {} is missing from the DAG",
                round, r
            );
            return false;
        };
        let previous_round_certificates: Vec<Certificate> = previous_round_map
            .values()
            .map(|(_, cert, _)| cert.clone())
            .collect();

        // Record that this round has been checked at least once. If the previous
        // round still contains bivalent vertices, later arrivals from the same
        // round are allowed to re-run this check with the larger local view.
        state.fast_path_checked_rounds.insert(round);

        let threshold = self.committee.quorum_threshold();
        let mut decide_one = Vec::new();
        let mut decide_zero = Vec::new();
        let mut undecided = Vec::new();

        for certificate in &previous_round_certificates {
            let candidate_header_id = certificate.header.id.clone();
            let candidate_digest = certificate.digest();
            let support_stake: Stake = current_round_certificates
                .iter()
                .filter(|x| {
                    x.header.solid_step_vertices.contains(&candidate_header_id)
                        || x.header.solid_step_vertices.contains(&candidate_digest)
                })
                .map(|x| self.committee.stake(&x.origin()))
                .sum();
            let reject_stake = current_round_stake.saturating_sub(support_stake);

            if support_stake >= threshold {
                decide_one.push(certificate.clone());
            } else if reject_stake >= threshold {
                decide_zero.push(certificate.clone());
            } else {
                undecided.push(certificate.clone());
            }
        }

        for certificate in &decide_one {
            state.set_commit_status(certificate, CommitStatus::OneValent);
        }
        for certificate in &decide_zero {
            state.set_commit_status(certificate, CommitStatus::ZeroValent);
        }
        for certificate in &undecided {
            state.set_commit_status(certificate, CommitStatus::Bivalent);
        }

        info!(
            "FAST_PATH_CHECK round={} threshold={} undecided={} decide_zero={} decide_one={}",
            round,
            threshold,
            undecided.len(),
            decide_zero.len(),
            decide_one.len()
        );

        if undecided.is_empty() {
            let to_commit = self.collect_fast_path_commits(&decide_one, state);
            let mut committed = Vec::new();
            for certificate in to_commit {
                // If a round is buffered, fast path can only commit it when checking that
                // exact round (target round = r). Other buffered rounds must wait for slow path.
                if state.buffered_rounds.contains(&certificate.round()) && certificate.round() != r {
                    continue;
                }
                if !state.record_commit(&certificate, false) {
                    continue;
                }
                committed.push(certificate);
            }
            state.cleanup_committed_history(self.gc_depth);
            state.refresh_buffered_rounds();
            let committed_any = !committed.is_empty();
            self.emit_commits("fast", committed).await;
            return committed_any;
        } else {
            state.note_buffered_round(r);
            info!(
                "FAST_PATH_DEFER round={} pending_round={} undecided={}",
                round,
                r,
                undecided.len()
            );
            return false;
        }
    }

    async fn slow_path(&self, round: Round, state: &mut State) -> bool {
        // The current slow-path implementation follows the sigma=1 Chitu flow:
        // a buffered round r is decided by the leader of round r+2, whose validity
        // is checked from round r+3, and whose strong-observe relation to round r
        // is witnessed by at least f+1 bridge vertices from round r+1.
        if round < 3 {
            return false;
        }

        let mut validity_cache = HashMap::new();
        let mut decided_any = false;
        let mut buffered_rounds: Vec<_> = state.buffered_rounds.iter().copied().collect();
        buffered_rounds.sort_unstable();

        for buffered_round in buffered_rounds {
            if buffered_round + 3 > round {
                continue;
            }
            if self.try_decide_buffered_round(buffered_round, state, &mut validity_cache) {
                decided_any = true;
            }
        }

        let sequence = self.collect_committable_buffered_rounds(state);
        if sequence.is_empty() {
            state.refresh_buffered_rounds();
            return decided_any;
        }

        state.cleanup_committed_history(self.gc_depth);
        state.refresh_buffered_rounds();
        self.emit_commits("slow", sequence).await;
        true
    }

    /// Map authority public key to node id (0..n-1), same as visualize_dag / extract_dag_out.
    fn author_to_node_id(&self, author: PublicKey) -> usize {
        self.author_to_node.get(&author).copied().unwrap_or(999)
    }

    /// Returns the certificate (and the certificate's digest) originated by the leader of the
    /// specified round (if any).
    fn leader<'a>(&self, round: Round, dag: &'a Dag) -> Option<&'a DagEntry> {
        // TODO: We should elect the leader of round r-2 using the common coin revealed at round r.
        // At this stage, we are guaranteed to have 2f+1 certificates from round r (which is enough to
        // compute the coin). We currently just use round-robin.
        #[cfg(test)]
        let coin = 0;
        #[cfg(not(test))]
        let coin = round;

        // Elect the leader.
        let leader = self.authorities[coin as usize % self.authorities.len()];

        // Return its certificate and the certificate's digest.
        dag.get(&round).map(|x| x.get(&leader)).flatten()
    }

    fn is_valid_leader_round(
        &self,
        leader_round: Round,
        state: &State,
        cache: &mut HashMap<Round, bool>,
    ) -> bool {
        if let Some(valid) = cache.get(&leader_round) {
            return *valid;
        }

        let Some((leader_digest, leader, _status)) = self.leader(leader_round, &state.dag) else {
            cache.insert(leader_round, false);
            return false;
        };

        let direct_support_round = leader_round + 1;
        let threshold = self.committee.validity_threshold();
        let direct_support: Stake = state
            .dag
            .get(&direct_support_round)
            .map(|support_round| {
                support_round
                    .values()
                    .filter(|(_digest, certificate, _status)| {
                        certificate.header.parents.contains(leader_digest)
                    })
                    .map(|(_digest, certificate, _status)| self.committee.stake(&certificate.origin()))
                    .sum()
            })
            .unwrap_or(0);

        if direct_support >= threshold {
            cache.insert(leader_round, true);
            return true;
        }

        let next_leader_round = leader_round + 1;
        let recursive_valid = match self.leader(next_leader_round, &state.dag) {
            Some((_next_digest, next_leader, _next_status)) => {
                self.is_valid_leader_round(next_leader_round, state, cache)
                    && next_leader.header.parents.contains(leader_digest)
            }
            None => false,
        };

        if recursive_valid {
            let leader_node = self.author_to_node_id(leader.origin());
            info!(
                "LEADER_VALIDITY_RECURSIVE leader_round={} leader_node={} promoted_by_round={}",
                leader_round,
                leader_node,
                next_leader_round
            );
        }
        cache.insert(leader_round, recursive_valid);
        recursive_valid
    }

    fn try_decide_buffered_round(
        &self,
        target_round: Round,
        state: &mut State,
        validity_cache: &mut HashMap<Round, bool>,
    ) -> bool {
        let bridge_round = target_round + 1;
        let leader_round = target_round + 2;

        let Some((_leader_digest, leader, _status)) = self.leader(leader_round, &state.dag) else {
            return false;
        };
        if !self.is_valid_leader_round(leader_round, state, validity_cache) {
            return false;
        }

        let bridge_round_entries: Vec<_> = match state.dag.get(&bridge_round) {
            Some(round_map) => round_map
                .values()
                .map(|(digest, certificate, status)| (digest.clone(), certificate.clone(), *status))
                .collect(),
            None => return false,
        };
        let target_round_entries: Vec<_> = match state.dag.get(&target_round) {
            Some(round_map) => round_map
                .values()
                .map(|(digest, certificate, status)| (digest.clone(), certificate.clone(), *status))
                .collect(),
            None => return false,
        };
        if bridge_round_entries.is_empty() || target_round_entries.is_empty() {
            return false;
        }

        let threshold = self.committee.validity_threshold();
        let leader_node = self.author_to_node_id(leader.origin());
        let bivalent_candidates: Vec<_> = target_round_entries
            .iter()
            .filter_map(|(_digest, certificate, status)| {
                if *status == CommitStatus::Bivalent {
                    Some(certificate.clone())
                } else {
                    None
                }
            })
            .collect();
        if bivalent_candidates.is_empty() {
            return false;
        }

        let leader_parents = leader.header.parents.clone();
        let mut changed = false;

        for candidate in bivalent_candidates {
            let candidate_digest = candidate.digest();
            let support_stake: Stake = bridge_round_entries
                .iter()
                .filter(|(bridge_digest, bridge_certificate, _status)| {
                    leader_parents.contains(bridge_digest)
                        && bridge_certificate.header.parents.contains(&candidate_digest)
                })
                .map(|(_bridge_digest, bridge_certificate, _status)| {
                    self.committee.stake(&bridge_certificate.origin())
                })
                .sum();
            let node_id = self.author_to_node_id(candidate.origin());
            let status = if support_stake >= threshold {
                CommitStatus::OneValent
            } else {
                CommitStatus::ZeroValent
            };
            info!(
                "SLOW_PATH_DECISION target_round={} leader_round={} leader_node={} candidate_round={} candidate_node={} bridge_round={} support_stake={} threshold={} decision={}",
                target_round,
                leader_round,
                leader_node,
                candidate.round(),
                node_id,
                bridge_round,
                support_stake,
                threshold,
                match status {
                    CommitStatus::OneValent => "one",
                    CommitStatus::ZeroValent => "zero",
                    _ => "unknown",
                }
            );
            state.set_commit_status(&candidate, status);
            changed = true;
        }

        changed
    }

    fn round_is_decided(&self, round: Round, state: &State) -> bool {
        state
            .dag
            .get(&round)
            .map(|authorities| {
                authorities.values().all(|(_digest, _certificate, status)| {
                    *status == CommitStatus::OneValent || *status == CommitStatus::ZeroValent
                })
            })
            .unwrap_or(false)
    }

    fn collect_committable_buffered_rounds(&self, state: &mut State) -> Vec<Certificate> {
        let mut rounds: Vec<_> = state.buffered_rounds.iter().copied().collect();
        rounds.sort_unstable();

        let mut sequence = Vec::new();
        for round in rounds {
            if !self.round_is_decided(round, state) {
                break;
            }

            let one_valent: Vec<_> = state
                .dag
                .get(&round)
                .map(|authorities| {
                    authorities
                        .values()
                        .filter_map(|(_digest, certificate, status)| {
                            if *status == CommitStatus::OneValent {
                                Some(certificate.clone())
                            } else {
                                None
                            }
                        })
                        .collect()
                })
                .unwrap_or_default();

            for certificate in self.collect_fast_path_commits(&one_valent, state) {
                if state.record_commit(&certificate, true) {
                    sequence.push(certificate);
                }
            }
            state.buffered_rounds.remove(&round);
        }

        sequence.sort_by_key(|certificate| certificate.round());
        sequence
    }

    fn order_leaders(&self, leader: &Certificate, state: &State) -> Vec<Certificate> {
        let wave = self.committee.solid_wave_length() as usize;
        if wave == 0 {
            return vec![leader.clone()];
        }

        let start = state
            .last_committed_leader_round
            .saturating_add(wave as u64);
        let end_round = leader.round();
        let mut to_commit = vec![leader.clone()];
        let mut cur = leader;
        for r in (start..end_round).rev().step_by(wave) {
            let (_, prev_leader, _) = match self.leader(r, &state.dag) {
                Some(x) => x,
                None => continue,
            };
            if self.linked(cur, prev_leader, state) {
                to_commit.push(prev_leader.clone());
                cur = prev_leader;
            }
        }
        to_commit
    }

    /// Find a parent certificate by digest in the immediately previous round.
    fn find_parent_certificate<'a>(
        &self,
        state: &'a State,
        child_round: Round,
        parent_digest: &Digest,
    ) -> Option<&'a DagEntry> {
        if child_round <= 1 {
            return None;
        }
        state
            .find_certificate(parent_digest)
            .filter(|(_, certificate, _)| certificate.round() + 1 == child_round)
    }

    /// Checks if there is a path between two leaders using strong parent edges only.
    fn linked(&self, leader: &Certificate, prev_leader: &Certificate, state: &State) -> bool {
        let target = prev_leader.digest();
        let mut stack = vec![leader];
        let mut visited = HashSet::new();

        while let Some(current) = stack.pop() {
            let current_digest = current.digest();
            if !visited.insert(current_digest.clone()) {
                continue;
            }
            if current_digest == target {
                return true;
            }

            for parent in &current.header.parents {
                if let Some((_, parent_cert, _)) =
                    self.find_parent_certificate(state, current.round(), parent)
                {
                    stack.push(parent_cert);
                }
            }
        }
        false
    }

    /// Flatten the dag referenced by the input certificate. This is a classic depth-first search (pre-order):
    /// https://en.wikipedia.org/wiki/Tree_traversal#Pre-order
    fn order_dag(&self, leader: &Certificate, state: &State) -> Vec<Certificate> {
        debug!("Processing sub-dag of {:?}", leader);
        let mut ordered = Vec::new();
        let mut already_ordered: HashSet<Digest> = HashSet::new();

        let mut buffer = vec![leader];
        while let Some(x) = buffer.pop() {
            let x_digest = x.digest();
            let already_committed = state.committed_digests.contains(&x_digest);
            if already_ordered.contains(&x_digest) || already_committed {
                continue;
            }
            already_ordered.insert(x_digest);

            debug!("Sequencing {:?}", x);
            ordered.push(x.clone());
            for parent in &x.header.parents {
                let (digest, certificate, status) =
                    match self.find_parent_certificate(state, x.round(), parent) {
                        Some(x) => x,
                        None => continue, // Parent already GC'ed or not in local DAG.
                    };

                if *status == CommitStatus::ZeroValent {
                    continue;
                }

                // We skip the certificate if we (1) already processed it or (2) we reached a round that we already
                // committed before.
                let mut skip = already_ordered.contains(digest);
                skip |= state.committed_digests.contains(digest);
                if !skip {
                    buffer.push(certificate);
                }
            }
        }

        // Ensure we do not commit garbage collected certificates.
        ordered.retain(|x| x.round() + self.gc_depth >= state.last_committed_round);

        // Ordering the output by round is not really necessary but it makes the commit sequence prettier.
        ordered.sort_by_key(|x| x.round());
        ordered
    }

    fn collect_fast_path_commits(
        &self,
        one_valent: &[Certificate],
        state: &State,
    ) -> Vec<Certificate> {
        let mut to_commit = Vec::new();
        let mut seen = HashSet::new();

        for certificate in one_valent {
            for ancestor in self.order_dag(certificate, state) {
                if seen.insert(ancestor.digest()) {
                    to_commit.push(ancestor);
                }
            }
        }

        to_commit.sort_by_key(|certificate| certificate.round());
        to_commit
    }

    fn collect_due_buffered_from_leader(
        &self,
        leaders: &[Certificate],
        leader_round: Round,
        wave_length: Round,
        state: &mut State,
        sequence: &mut Vec<Certificate>,
    ) {
        if leaders.is_empty() || wave_length == 0 {
            return;
        }

        let mut due_rounds: Vec<_> = state
            .buffered_rounds
            .iter()
            .copied()
            .filter(|buffered_round| {
                if leader_round <= *buffered_round {
                    return false;
                }
                let nearest_greater_leader = ((*buffered_round / wave_length) + 1) * wave_length;
                nearest_greater_leader <= leader_round
            })
            .collect();
        due_rounds.sort_unstable();

        for round in due_rounds {
            let Some(round_map) = state.dag.get(&round) else {
                continue;
            };
            let round_certs: Vec<_> = round_map
                .values()
                .map(|(_digest, certificate, status)| (certificate.clone(), *status))
                .collect();

            for (certificate, status) in round_certs {
                if status == CommitStatus::ZeroValent {
                    continue;
                }
                if state.committed_digests.contains(&certificate.digest()) {
                    continue;
                }

                // Only backtrack nodes that are reachable from the committing leader chain.
                let reachable = leaders
                    .iter()
                    .any(|leader_certificate| self.linked(leader_certificate, &certificate, state));
                if !reachable {
                    continue;
                }

                for candidate in self.order_dag(&certificate, state) {
                    if state.record_commit(&candidate, true) {
                        sequence.push(candidate);
                    }
                }
            }
        }
    }

    fn visualize_dag(&self, state: &State, current_round: Round) {
        // from current_round to round 1, reverse
        for round in (1..=current_round).rev() {
            if state.dag.contains_key(&round) {
                let round_certs = state.dag.get(&round).unwrap();
                let mut round_output = format!("Round {}:", round);
                let mut vertices = Vec::new();

                let mut sorted_certs: Vec<_> = round_certs.iter().collect();
                sorted_certs.sort_by_key(|(author, _)| *author);

                for (author, (_cert_digest, certificate, status)) in sorted_certs {
                    let node_id = self.author_to_node.get(author).unwrap_or(&999);
                    let vertex_name = format!("Vertex{}", node_id);

                    // find the parent nodes
                    let mut parents = Vec::new();
                    let mut weak_parents = Vec::new();
                    for parent_digest in &certificate.header.parents {
                        // find the parent certificate in the dag
                        if let Some((parent_round, parent_author)) =
                            self.find_certificate_in_dag(state, parent_digest)
                        {
                            let parent_node_id =
                                self.author_to_node.get(&parent_author).unwrap_or(&999);
                            let is_weak = parent_round + 1 != round;
                            if is_weak {
                                let weak_entry = format!("[w{},{}]", parent_round, parent_node_id);
                                parents.push(weak_entry.clone());
                                weak_parents.push(weak_entry);
                            } else {
                                parents.push(format!("[{},{}]", parent_round, parent_node_id));
                            }
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
                    for digest in &certificate.header.solid_wave_vertices {
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
                    for digest in &certificate.header.solid_wave_vertices_merged {
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

                    let status_str = match status {
                        CommitStatus::OneValent => " one-valent",
                        CommitStatus::ZeroValent => " zero-valent",
                        CommitStatus::Bivalent => " bivalent",
                        CommitStatus::Pending => "",
                    };

                    let vertex_str = if weak_parents.is_empty() {
                        format!(
                            "({}){} (solid_wave_vertices: {}){}{}{}",
                            vertex_name,
                            parent_str,
                            certificate.header.solid_wave_vertices.len(),
                            solid_str,
                            merged_str,
                            status_str
                        )
                    } else {
                        format!(
                            "({}){} weak=[{}] (solid_wave_vertices: {}){}{}{}",
                            vertex_name,
                            parent_str,
                            weak_parents.join(", "),
                            certificate.header.solid_wave_vertices.len(),
                            solid_str,
                            merged_str,
                            status_str
                        )
                    };
                    vertices.push(vertex_str);
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
        state.find_digest(digest)
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

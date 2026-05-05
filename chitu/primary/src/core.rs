use crate::aggregators::{CertificatesAggregator, VotesAggregator};
use crate::error::{DagError, DagResult};
use crate::messages::{Certificate, Header, HeaderBundle, ProposalParents, Vote};
use crate::primary::{PrimaryMessage, Round};
use crate::synchronizer::Synchronizer;
use async_recursion::async_recursion;
use bytes::Bytes;
use config::Committee;
use crypto::Hash as _;
use crypto::{Digest, PublicKey, SignatureService};
use log::{debug, error, warn};
#[cfg(feature = "benchmark")]
use log::info;
use network::{CancelHandler, ReliableSender};
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use store::Store;
use tokio::sync::mpsc::{Receiver, Sender};
use tokio::time::{Duration, Instant};

#[cfg(test)]
#[path = "tests/core_tests.rs"]
pub mod core_tests;

#[derive(Default)]
struct PrepareSupport {
    weight: u64,
    voters: HashSet<PublicKey>,
}

#[derive(Default)]
struct VertexRoundState {
    known_digest: Option<Digest>,
    certified_digest: Option<Digest>,
    prepare_support: HashMap<Digest, PrepareSupport>,
    equivocating: bool,
}

struct AdaptiveWaitState {
    proposal_parents: ProposalParents,
    waiting_vertices: HashMap<PublicKey, Digest>,
    started_at: Instant,
    initial_parent_count: usize,
    initial_parent_digests: HashSet<Digest>,
    extensions: usize,
}

#[derive(Default)]
struct WaitCandidateSummary {
    authors_seen: usize,
    known_vertices: usize,
    known_and_fplus1: usize,
    known_but_support_insufficient: usize,
    fplus1_without_header: usize,
    delivered_filtered: usize,
    equivocation_filtered: usize,
    waiting_final: usize,
}

pub struct Core {
    /// The public key of this primary.
    name: PublicKey,
    /// The committee information.
    committee: Committee,
    /// The persistent storage.
    store: Store,
    /// Handles synchronization with other nodes and our workers.
    synchronizer: Synchronizer,
    /// Service to sign headers.
    signature_service: SignatureService,
    /// The current consensus round (used for cleanup).
    consensus_round: Arc<AtomicU64>,
    /// The depth of the garbage collector.
    gc_depth: Round,
    /// Whether adaptive wait is enabled for this primary.
    adaptive_wait_enabled: bool,

    /// Receiver for dag messages (headers, votes, certificates).
    rx_primaries: Receiver<PrimaryMessage>,
    /// Receives loopback headers from the `HeaderWaiter`.
    rx_header_waiter: Receiver<Header>,
    /// Receives loopback certificates from the `CertificateWaiter`.
    rx_certificate_waiter: Receiver<Certificate>,
    /// Receives our newly created headers from the `Proposer`.
    rx_proposer: Receiver<Header>,
    /// Output all certificates to the consensus layer.
    tx_consensus: Sender<Certificate>,
    /// Send valid parent snapshots to the `Proposer` (along with their round).
    tx_proposer: Sender<(ProposalParents, Round)>,

    /// The last garbage collected round.
    gc_round: Round,
    /// The authors of the last voted headers.
    last_voted: HashMap<Round, HashSet<PublicKey>>,
    /// The set of headers we are currently processing.
    processing: HashMap<Round, HashSet<Digest>>,
    /// Headers known locally (keyed by header id).
    known_headers: HashMap<Digest, Header>,
    /// Our locally proposed headers waiting for quorum (keyed by header id).
    pending_headers: HashMap<Digest, Header>,
    /// One vote aggregator per locally known header.
    votes_aggregators: HashMap<Digest, VotesAggregator>,
    /// Votes received before their corresponding header becomes locally known.
    buffered_votes: HashMap<Digest, Vec<Vote>>,
    /// Tracks headers for which a certificate has already been formed or delivered.
    certified_headers: HashMap<Digest, Round>,
    /// Aggregates certificates to use as parents for new headers.
    certificates_aggregators: HashMap<Round, Box<CertificatesAggregator>>,
    /// Tracks prepare-like support for headers of each (round, origin).
    vertex_round_states: HashMap<Round, HashMap<PublicKey, VertexRoundState>>,
    /// Adaptive wait state keyed by the parent round that is about to unlock the next round.
    adaptive_wait_rounds: HashMap<Round, AdaptiveWaitState>,
    /// Rounds that already paid the adaptive-wait gap and can be refreshed directly.
    adaptive_wait_released: HashSet<Round>,
    /// Short adaptive wait window; renewed whenever a waiting round observes progress.
    adaptive_wait_delay: Duration,
    /// A network sender to send the batches to the other workers.
    network: ReliableSender,
    /// Keeps the cancel handlers of the messages we sent.
    cancel_handlers: HashMap<Round, Vec<CancelHandler>>,
}

impl Core {
    fn node_index(&self, key: &PublicKey) -> Option<usize> {
        self.committee
            .authorities
            .keys()
            .position(|authority| authority == key)
    }

    fn merge_proposal_parents(existing: &mut ProposalParents, update: ProposalParents) -> bool {
        let old_parents = existing.parents.len();
        let old_step = existing.solid_step_union.len();
        let old_wave = existing.solid_wave_union.len();

        let mut merged_parents: HashSet<Digest> = existing.parents.drain(..).collect();
        merged_parents.extend(update.parents);
        existing.parents = merged_parents.into_iter().collect();
        existing.solid_step_union.extend(update.solid_step_union);
        existing.solid_wave_union.extend(update.solid_wave_union);

        existing.parents.len() != old_parents
            || existing.solid_step_union.len() != old_step
            || existing.solid_wave_union.len() != old_wave
    }

    #[cfg(feature = "benchmark")]
    fn sorted_digest_strings<'a, I>(digests: I) -> Vec<String>
    where
        I: IntoIterator<Item = &'a Digest>,
    {
        let mut values: Vec<_> = digests
            .into_iter()
            .map(|digest| format!("{:?}", digest))
            .collect();
        values.sort();
        values
    }

    #[cfg(feature = "benchmark")]
    fn gained_parent_digest_strings(state: &AdaptiveWaitState) -> Vec<String> {
        Self::sorted_digest_strings(
            state
                .proposal_parents
                .parents
                .iter()
                .filter(|digest| !state.initial_parent_digests.contains(*digest)),
        )
    }

    #[cfg(feature = "benchmark")]
    fn format_digest_list(digests: &[String]) -> String {
        if digests.is_empty() {
            "-".to_string()
        } else {
            digests.join(",")
        }
    }

    async fn delivered_header_ids_for_parents(
        &mut self,
        parents: &[Digest],
    ) -> HashSet<Digest> {
        let mut delivered = HashSet::with_capacity(parents.len());
        for parent_digest in parents {
            if let Ok(Some(bytes)) = self.store.read(parent_digest.to_vec()).await {
                if let Ok(certificate) = bincode::deserialize::<Certificate>(&bytes) {
                    delivered.insert(certificate.header.id);
                }
            }
        }
        delivered
    }

    fn record_processed_header(&mut self, header: &Header) -> bool {
        let state = self
            .vertex_round_states
            .entry(header.round)
            .or_insert_with(HashMap::new)
            .entry(header.author)
            .or_insert_with(VertexRoundState::default);

        match &state.known_digest {
            Some(existing) if existing != &header.id => {
                state.equivocating = true;
                true
            }
            Some(_) => false,
            None => {
                state.known_digest = Some(header.id.clone());
                true
            }
        }
    }

    fn record_prepare_vote(&mut self, vote: &Vote) -> bool {
        let threshold = self.committee.validity_threshold() as u64;
        let state = self
            .vertex_round_states
            .entry(vote.round)
            .or_insert_with(HashMap::new)
            .entry(vote.origin)
            .or_insert_with(VertexRoundState::default);

        let support = state
            .prepare_support
            .entry(vote.id.clone())
            .or_insert_with(PrepareSupport::default);
        let inserted = support.voters.insert(vote.author);
        if inserted {
            support.weight += self.committee.stake(&vote.author) as u64;
        }

        if state.equivocating {
            return inserted;
        }

        if let Some(known) = &state.known_digest {
            if known != &vote.id && support.weight >= threshold {
                state.equivocating = true;
                return true;
            }
        }
        if let Some(certified) = &state.certified_digest {
            if certified != &vote.id && support.weight >= threshold {
                state.equivocating = true;
                return true;
            }
        }

        let sufficiently_supported = state
            .prepare_support
            .values()
            .filter(|candidate| candidate.weight >= threshold)
            .count();
        if sufficiently_supported > 1 {
            state.equivocating = true;
            return true;
        }

        inserted
    }

    fn record_certificate_delivery(&mut self, certificate: &Certificate) -> bool {
        let threshold = self.committee.validity_threshold() as u64;
        let state = self
            .vertex_round_states
            .entry(certificate.round())
            .or_insert_with(HashMap::new)
            .entry(certificate.origin())
            .or_insert_with(VertexRoundState::default);

        if let Some(existing) = &state.certified_digest {
            if existing == &certificate.header.id {
                return false;
            }
            state.equivocating = true;
            return true;
        }

        if let Some(known) = &state.known_digest {
            if known != &certificate.header.id {
                state.equivocating = true;
                return true;
            }
        }

        state.certified_digest = Some(certificate.header.id.clone());
        state.known_digest = Some(certificate.header.id.clone());

        let support = state
            .prepare_support
            .entry(certificate.header.id.clone())
            .or_insert_with(PrepareSupport::default);
        for (author, _) in &certificate.votes {
            if support.voters.insert(*author) {
                support.weight += self.committee.stake(author) as u64;
            }
        }

        for (digest, support) in &state.prepare_support {
            if digest != &certificate.header.id && support.weight >= threshold {
                state.equivocating = true;
                return true;
            }
        }

        true
    }

    async fn rebuild_waiting_vertices(
        &mut self,
        round: Round,
        state: &mut AdaptiveWaitState,
    ) -> (bool, WaitCandidateSummary) {
        let old_waiting = state.waiting_vertices.clone();
        state.waiting_vertices.clear();
        let mut summary = WaitCandidateSummary::default();

        let delivered = self
            .delivered_header_ids_for_parents(&state.proposal_parents.parents)
            .await;
        let prepare_threshold = self.committee.validity_threshold() as u64;
        if let Some(per_author) = self.vertex_round_states.get(&round) {
            summary.authors_seen = per_author.len();
            for (origin, vertex_state) in per_author {
                if vertex_state.equivocating {
                    summary.equivocation_filtered += 1;
                    continue;
                }
                let has_fplus1_support = vertex_state
                    .prepare_support
                    .values()
                    .any(|support| support.weight >= prepare_threshold);
                let digest = match vertex_state.known_digest.clone() {
                    Some(digest) => {
                        summary.known_vertices += 1;
                        let known_support = vertex_state
                            .prepare_support
                            .get(&digest)
                            .map(|support| support.weight)
                            .unwrap_or_default();
                        if known_support < prepare_threshold {
                            summary.known_but_support_insufficient += 1;
                            continue;
                        }
                        summary.known_and_fplus1 += 1;
                        digest
                    }
                    None => {
                        if has_fplus1_support {
                            summary.fplus1_without_header += 1;
                        }
                        continue;
                    }
                };
                if delivered.contains(&digest) {
                    summary.delivered_filtered += 1;
                    continue;
                }
                state.waiting_vertices.insert(*origin, digest);
            }
        }

        summary.waiting_final = state.waiting_vertices.len();
        (state.waiting_vertices != old_waiting, summary)
    }

    async fn track_adaptive_wait_progress(&mut self, round: Round) -> bool {
        if !self.adaptive_wait_enabled {
            return false;
        }
        let mut changed = false;
        if let Some(mut state) = self.adaptive_wait_rounds.remove(&round) {
            (changed, _) = self.rebuild_waiting_vertices(round, &mut state).await;
            if changed && !state.waiting_vertices.is_empty() {
                state.extensions += 1;
            }
            self.adaptive_wait_rounds.insert(round, state);
        }
        changed
    }

    async fn broadcast_sync_certificate(&mut self, certificate: &Certificate) {
        let cert_id = certificate.header.id.clone();
        let cert_round = certificate.round();
        debug!(
            "Broadcasting weak certificate {} (round {}) to other primaries",
            cert_id, cert_round
        );
        let addresses: Vec<_> = self
            .committee
            .others_primaries(&self.name)
            .iter()
            .map(|(_, x)| x.primary_to_primary)
            .collect();
        let bytes = bincode::serialize(&PrimaryMessage::SyncWeakCertificate(certificate.clone()))
            .expect("Failed to serialize our own weak certificate");
        for address in addresses {
            let handler = self.network.send(address, Bytes::from(bytes.clone())).await;
            let id = cert_id.clone();
            tokio::spawn(async move {
                match handler.await {
                    Ok(_) => {
                        debug!(
                            "Weak certificate {} (round {}) successfully delivered to primary {}",
                            id, cert_round, address
                        );
                    }
                    Err(_) => {
                        debug!(
                            "Weak certificate {} (round {}) delivery to primary {} was canceled or failed",
                            id, cert_round, address
                        );
                    }
                }
            });
        }
    }

    async fn broadcast_waiting_weak_certificates(
        &mut self,
        waiting_vertices: &HashMap<PublicKey, Digest>,
    ) {
        let waiting_ids: Vec<_> = waiting_vertices.values().cloned().collect();
        let validity_threshold = self.committee.validity_threshold();
        for digest in waiting_ids {
            if self.certified_headers.contains_key(&digest) {
                continue;
            }

            let header = match self.known_headers.get(&digest) {
                Some(header) => header.clone(),
                None => continue,
            };

            let weak_certificate = self
                .votes_aggregators
                .get_mut(&digest)
                .and_then(|aggregator| aggregator.weak_certificate_if_ready(validity_threshold, &header));

            if let Some(weak_certificate) = weak_certificate {
                self.broadcast_sync_certificate(&weak_certificate).await;
            }
        }
    }

    async fn bundled_parent_certificates(&mut self, header: &Header) -> DagResult<Vec<Certificate>> {
        let mut certificates = Vec::with_capacity(header.parents.len());
        for digest in &header.parents {
            let Some(bytes) = self.store.read(digest.to_vec()).await? else {
                continue;
            };
            let certificate = bincode::deserialize(&bytes)?;
            certificates.push(certificate);
        }
        Ok(certificates)
    }

    async fn process_header_bundle(&mut self, bundle: HeaderBundle) -> DagResult<()> {
        let HeaderBundle {
            header,
            parent_certificates,
        } = bundle;
        self.sanitize_header(&header)?;

        let mut expected_parents: HashSet<_> = header.parents.iter().cloned().collect();
        for certificate in parent_certificates {
            let digest = certificate.digest();
            if !expected_parents.remove(&digest) {
                debug!(
                    "Ignoring bundled certificate {} while processing header {}: not a referenced parent",
                    certificate.header.id, header.id
                );
                continue;
            }
            if self.certified_headers.get(&certificate.header.id) == Some(&certificate.round()) {
                continue;
            }
            self.sanitize_certificate(&certificate)?;
            self.process_certificate(certificate).await?;
        }

        self.process_header(&header).await
    }

    async fn update_adaptive_wait_round(
        &mut self,
        round: Round,
        parents: ProposalParents,
    ) -> DagResult<()> {
        if !self.adaptive_wait_enabled {
            self.tx_proposer
                .send((parents, round))
                .await
                .expect("Failed to send certificate");
            return Ok(());
        }

        if self.adaptive_wait_released.contains(&round) {
            self.tx_proposer
                .send((parents, round))
                .await
                .expect("Failed to send certificate");
            return Ok(());
        }

        let now = Instant::now();
        let had_existing_state = self.adaptive_wait_rounds.contains_key(&round);
        let mut state = self
            .adaptive_wait_rounds
            .remove(&round)
            .unwrap_or(AdaptiveWaitState {
                proposal_parents: ProposalParents::default(),
                waiting_vertices: HashMap::new(),
                started_at: now,
                initial_parent_count: 0,
                initial_parent_digests: HashSet::new(),
                extensions: 0,
            });
        let previous_parent_count = state.proposal_parents.parents.len();
        let previous_waiting_count = state.waiting_vertices.len();
        let mut observed_progress =
            Self::merge_proposal_parents(&mut state.proposal_parents, parents);
        let (waiting_changed, candidate_summary) =
            self.rebuild_waiting_vertices(round, &mut state).await;
        if waiting_changed {
            observed_progress = true;
        }

        if !had_existing_state {
            let decision = if state.waiting_vertices.is_empty() {
                "direct_parent"
            } else {
                "wait"
            };
            self.log_adaptive_wait_candidates(
                round,
                state.proposal_parents.parents.len(),
                &candidate_summary,
                decision,
            );
        }

        if state.waiting_vertices.is_empty() {
            if had_existing_state {
                self.log_adaptive_wait_release(round, &state, "resolved");
            }
            self.adaptive_wait_released.insert(round);
            self.tx_proposer
                .send((state.proposal_parents, round))
                .await
                .expect("Failed to send certificate");
            return Ok(());
        }

        if !had_existing_state {
            state.started_at = now;
            state.initial_parent_count = state.proposal_parents.parents.len();
            state.initial_parent_digests = state.proposal_parents.parents.iter().cloned().collect();
            self.log_adaptive_wait_start(round, &state);
            self.broadcast_waiting_weak_certificates(&state.waiting_vertices)
                .await;
        } else if observed_progress {
            state.extensions += 1;
            self.log_adaptive_wait_extend(
                round,
                &state,
                previous_parent_count,
                previous_waiting_count,
            );
        }
        self.adaptive_wait_rounds.insert(round, state);
        Ok(())
    }

    #[cfg(feature = "benchmark")]
    fn log_adaptive_wait_candidates(
        &self,
        round: Round,
        parent_count: usize,
        summary: &WaitCandidateSummary,
        decision: &str,
    ) {
        info!(
            "ADAPTIVE_WAIT_CANDIDATES round={} parents={} authors_seen={} known_vertices={} known_and_fplus1={} known_but_support_insufficient={} fplus1_without_header={} delivered_filtered={} equivocation_filtered={} waiting_final={} decision={}",
            round,
            parent_count,
            summary.authors_seen,
            summary.known_vertices,
            summary.known_and_fplus1,
            summary.known_but_support_insufficient,
            summary.fplus1_without_header,
            summary.delivered_filtered,
            summary.equivocation_filtered,
            summary.waiting_final,
            decision,
        );
    }

    #[cfg(not(feature = "benchmark"))]
    fn log_adaptive_wait_candidates(
        &self,
        _round: Round,
        _parent_count: usize,
        _summary: &WaitCandidateSummary,
        _decision: &str,
    ) {
    }

    #[cfg(feature = "benchmark")]
    fn log_adaptive_wait_start(&self, round: Round, state: &AdaptiveWaitState) {
        info!(
            "ADAPTIVE_WAIT_START round={} initial_parents={} waiting={} deadline_ms={}",
            round,
            state.initial_parent_count,
            state.waiting_vertices.len(),
            self.adaptive_wait_delay.as_millis()
        );
    }

    #[cfg(not(feature = "benchmark"))]
    fn log_adaptive_wait_start(&self, _round: Round, _state: &AdaptiveWaitState) {
    }

    #[cfg(feature = "benchmark")]
    fn log_adaptive_wait_extend(
        &self,
        round: Round,
        state: &AdaptiveWaitState,
        previous_parent_count: usize,
        previous_waiting_count: usize,
    ) {
        let gained_parent_digests = Self::gained_parent_digest_strings(state);
        info!(
            "ADAPTIVE_WAIT_EXTEND round={} parents_before={} parents_after={} waiting_before={} waiting_after={} extensions={} total_gained_parents={} gained_parent_digests={}",
            round,
            previous_parent_count,
            state.proposal_parents.parents.len(),
            previous_waiting_count,
            state.waiting_vertices.len(),
            state.extensions,
            gained_parent_digests.len(),
            Self::format_digest_list(&gained_parent_digests)
        );
    }

    #[cfg(not(feature = "benchmark"))]
    fn log_adaptive_wait_extend(
        &self,
        _round: Round,
        _state: &AdaptiveWaitState,
        _previous_parent_count: usize,
        _previous_waiting_count: usize,
    ) {
    }

    #[cfg(feature = "benchmark")]
    fn log_adaptive_wait_release(&self, round: Round, state: &AdaptiveWaitState, reason: &str) {
        let gained_parent_digests = Self::gained_parent_digest_strings(state);
        info!(
            "ADAPTIVE_WAIT_RELEASE round={} reason={} initial_parents={} final_parents={} gained_parents={} gained_parent_digests={} waiting_remaining={} extensions={} elapsed_ms={}",
            round,
            reason,
            state.initial_parent_count,
            state.proposal_parents.parents.len(),
            gained_parent_digests.len(),
            Self::format_digest_list(&gained_parent_digests),
            state.waiting_vertices.len(),
            state.extensions,
            state.started_at.elapsed().as_millis()
        );
    }

    #[cfg(not(feature = "benchmark"))]
    fn log_adaptive_wait_release(
        &self,
        _round: Round,
        _state: &AdaptiveWaitState,
        _reason: &str,
    ) {
    }

    async fn flush_resolved_adaptive_wait_rounds(&mut self) {
        if !self.adaptive_wait_enabled {
            return;
        }
        let ready_rounds: Vec<_> = self
            .adaptive_wait_rounds
            .iter()
            .filter_map(|(round, state)| state.waiting_vertices.is_empty().then_some(*round))
            .collect();

        for round in ready_rounds {
            if let Some(state) = self.adaptive_wait_rounds.remove(&round) {
                self.log_adaptive_wait_release(round, &state, "resolved");
                self.adaptive_wait_released.insert(round);
                self.tx_proposer
                    .send((state.proposal_parents, round))
                    .await
                    .expect("Failed to send certificate");
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub fn spawn(
        name: PublicKey,
        committee: Committee,
        store: Store,
        synchronizer: Synchronizer,
        signature_service: SignatureService,
        consensus_round: Arc<AtomicU64>,
        gc_depth: Round,
        adaptive_wait_enabled: bool,
        rx_primaries: Receiver<PrimaryMessage>,
        rx_header_waiter: Receiver<Header>,
        rx_certificate_waiter: Receiver<Certificate>,
        rx_proposer: Receiver<Header>,
        tx_consensus: Sender<Certificate>,
        tx_proposer: Sender<(ProposalParents, Round)>,
    ) {
        tokio::spawn(async move {
            Self {
                name,
                committee,
                store,
                synchronizer,
                signature_service,
                consensus_round,
                gc_depth,
                adaptive_wait_enabled,
                rx_primaries,
                rx_header_waiter,
                rx_certificate_waiter,
                rx_proposer,
                tx_consensus,
                tx_proposer,
                gc_round: 0,
                last_voted: HashMap::with_capacity(2 * gc_depth as usize),
                processing: HashMap::with_capacity(2 * gc_depth as usize),
                known_headers: HashMap::with_capacity(2 * gc_depth as usize),
                pending_headers: HashMap::with_capacity(2 * gc_depth as usize),
                votes_aggregators: HashMap::with_capacity(2 * gc_depth as usize),
                buffered_votes: HashMap::with_capacity(2 * gc_depth as usize),
                certified_headers: HashMap::with_capacity(2 * gc_depth as usize),
                certificates_aggregators: HashMap::with_capacity(2 * gc_depth as usize),
                vertex_round_states: HashMap::with_capacity(2 * gc_depth as usize),
                adaptive_wait_rounds: HashMap::with_capacity(2 * gc_depth as usize),
                adaptive_wait_released: HashSet::with_capacity(2 * gc_depth as usize),
                adaptive_wait_delay: Duration::from_millis(20),
                network: ReliableSender::new(),
                cancel_handlers: HashMap::with_capacity(2 * gc_depth as usize),
            }
            .run()
            .await;
        });
    }

    async fn process_own_header(&mut self, header: Header) -> DagResult<()> {
        // Track this locally proposed header independently so votes on current/future headers
        // can be aggregated in parallel.
        self.pending_headers
            .insert(header.id.clone(), header.clone());
        self.votes_aggregators
            .entry(header.id.clone())
            .or_insert_with(VotesAggregator::new);

        // Broadcast the new header in a reliable manner:
        // 1. Primary receives parents from `CertificateAggregator`
        // 2. Proposer creates header and sends it here
        // 3. Core broadcasts header to all other primaries
        debug!(
            "Broadcasting header {} (round {}) to other primaries",
            header.id, header.round
        );
        let parent_certificates = self.bundled_parent_certificates(&header).await?;
        debug!(
            "Bundling {} parent certificates with header {} (round {})",
            parent_certificates.len(),
            header.id,
            header.round
        );
        let addresses: Vec<_> = self
            .committee
            .others_primaries(&self.name)
            .iter()
            .map(|(_, x)| x.primary_to_primary)
            .collect();
        let bytes = bincode::serialize(&PrimaryMessage::HeaderBundle(HeaderBundle {
            header: header.clone(),
            parent_certificates,
        }))
        .expect("Failed to serialize our own header bundle");
        // Send to each primary individually so we can log per-node success/failure.
        let header_id = header.id.clone();
        let header_round = header.round;
        for address in addresses {
            let handler = self.network.send(address, Bytes::from(bytes.clone())).await;
            let id = header_id.clone();
            tokio::spawn(async move {
                match handler.await {
                    Ok(_) => {
                        debug!(
                            "Header {} (round {}) successfully delivered to primary {}",
                            id, header_round, address
                        );
                    }
                    Err(_) => {
                        debug!(
                            "Header {} (round {}) delivery to primary {} was canceled or failed",
                            id, header_round, address
                        );
                    }
                }
            });
        }

        // Process the header.
        self.process_header(&header).await
    }

    #[async_recursion]
    async fn process_header(&mut self, header: &Header) -> DagResult<()> {
        let origin_node = self
            .node_index(&header.author)
            .map_or_else(|| "unknown".to_string(), |idx| idx.to_string());
        debug!(
            "Received header {} (origin Node{}, round {}): entering processing pipeline",
            header.id, origin_node, header.round
        );
        debug!("Processing {:?}", header);
        // Indicate that we are processing this header.
        self.processing
            .entry(header.round)
            .or_insert_with(HashSet::new)
            .insert(header.id.clone());

        // Ensure we have the parents. If at least one parent is missing, the synchronizer returns an empty
        // vector; it will gather the missing parents (as well as all ancestors) from other nodes and then
        // reschedule processing of this header.
        let parents = self.synchronizer.get_parents(header).await?;
        if parents.is_empty() {
            debug!(
                "Header {} (round {}) suspended in synchronizer: missing parent(s), will be retried by HeaderWaiter",
                header.id,
                header.round
            );
            return Ok(());
        }

        // Check the parent certificates. Weak edges are disabled: every parent must come
        // from the immediately previous round and the processing quorum is evaluated from
        // that strong-parent set only.
        let round = header.round as u64;
        let is_solid_step = self.committee.is_solid_step(round);

        let mut stake = 0u64;
        let mut solid_step_union = HashSet::new();

        for x in &parents {
            ensure!(
                x.round() + 1 == round,
                DagError::MalformedHeader(header.id.clone())
            );
            stake += self.committee.stake(&x.origin()) as u64;
            if is_solid_step {
                solid_step_union.extend(x.header.solid_step_vertices_merged.iter().cloned());
            }
        }

        let threshold = self.committee.processing_threshold(round);
        if is_solid_step {
            ensure!(
                solid_step_union.len() >= threshold as usize,
                DagError::HeaderRequiresQuorum(header.id.clone())
            );
        } else {
            ensure!(
                stake >= threshold as u64,
                DagError::HeaderRequiresQuorum(header.id.clone())
            );
        }

        // Ensure we have the payload. If we don't, the synchronizer will ask our workers to get it, and then
        // reschedule processing of this header once we have it.
        if self.synchronizer.missing_payload(header).await? {
            debug!(
                "Header {} (round {}) suspended in synchronizer: missing payload, will be retried by HeaderWaiter, header={:?}",
                header.id,
                header.round,
                header
            );
            return Ok(());
        }

        // Store the header.
        let bytes = bincode::serialize(header).expect("Failed to serialize header");
        self.store.write(header.id.to_vec(), bytes).await;
        self.known_headers.insert(header.id.clone(), header.clone());
        self.votes_aggregators
            .entry(header.id.clone())
            .or_insert_with(VotesAggregator::new);
        let header_progress = self.record_processed_header(header);
        if header_progress {
            self.track_adaptive_wait_progress(header.round).await;
        }

        if let Some(buffered_votes) = self.buffered_votes.remove(&header.id) {
            for vote in buffered_votes {
                self.process_vote(vote).await?;
            }
        }

        // Check if we can vote for this header.
        let already_voted = self
            .last_voted
            .entry(header.round)
            .or_insert_with(HashSet::new)
            .contains(&header.author);

        if already_voted {
            debug!(
                "Discarding header {} (round {}): already voted for author in this round",
                header.id, header.round
            );
        } else {
            // Mark that we're voting for this author in this round.
            self.last_voted
                .entry(header.round)
                .or_insert_with(HashSet::new)
                .insert(header.author);

            // Make a vote and send it to the header's creator.
            let vote = Vote::new(header, &self.name, &mut self.signature_service).await;
            debug!(
                "Created vote {:?} for header {} (round {})",
                vote, header.id, header.round
            );
            debug!(
                "Processing local prepare-vote for header {} (round {}) before broadcast",
                header.id, header.round
            );
            self.process_vote(vote.clone())
                .await
                .expect("Failed to process our own vote");

            let addresses: Vec<_> = self
                .committee
                .others_primaries(&self.name)
                .iter()
                .map(|(_, x)| x.primary_to_primary)
                .collect();
            let bytes = bincode::serialize(&PrimaryMessage::Vote(vote))
                .expect("Failed to serialize our own vote");
            for address in addresses {
                let handler = self.network.send(address, Bytes::from(bytes.clone())).await;
                debug!(
                    "Broadcasting vote for header {} (round {}) to primary at {}",
                    header.id, header.round, address
                );
                self.cancel_handlers
                    .entry(header.round)
                    .or_insert_with(Vec::new)
                    .push(handler);
            }
        }
        Ok(())
    }

    #[async_recursion]
    async fn process_vote(&mut self, vote: Vote) -> DagResult<()> {
        debug!("Processing {:?}", vote);
        let vote_id = vote.id.clone();
        let round = vote.round;
        let vote_progress = self.record_prepare_vote(&vote);
        if vote_progress {
            self.track_adaptive_wait_progress(round).await;
        }

        if self.certified_headers.contains_key(&vote_id) {
            debug!(
                "Ignoring vote for already certified header {} (round {})",
                vote_id, vote.round
            );
            return Ok(());
        }

        let header = match self.known_headers.get(&vote_id) {
            Some(header) => header.clone(),
            None => {
                debug!(
                    "Buffering vote for header {} (round {}) until the header is locally available",
                    vote_id, vote.round
                );
                self.buffered_votes
                    .entry(vote_id)
                    .or_insert_with(Vec::new)
                    .push(vote);
                return Ok(());
            }
        };

        // Add it to the votes' aggregator and try to make a new certificate.
        let aggregator = self
            .votes_aggregators
            .entry(vote_id.clone())
            .or_insert_with(VotesAggregator::new);
        if let Some(certificate) = aggregator.append(vote, &self.committee, &header)? {
            self.certified_headers
                .insert(vote_id.clone(), certificate.round());
            self.pending_headers.remove(&vote_id);
            self.votes_aggregators.remove(&vote_id);
            self.buffered_votes.remove(&vote_id);
            let origin = certificate.origin();
            let origin_node = self
                .node_index(&origin)
                .map_or_else(|| "unknown".to_string(), |idx| idx.to_string());
            debug!(
                "Created certificate {} (origin Node{}, round {})",
                certificate.header.id,
                origin_node,
                certificate.round()
            );
            debug!(
                "Assembled {:?}, generated by node {} and header round is {}",
                certificate,
                certificate.origin(),
                header.round
            );

            // Process the new certificate.
            self.process_certificate(certificate)
                .await
                .expect("Failed to process valid certificate");
        }
        Ok(())
    }

    #[async_recursion]
    async fn process_sync_weak_certificate(&mut self, certificate: Certificate) -> DagResult<()> {
        let header = certificate.header.clone();
        let origin = certificate.origin();
        let origin_node = self
            .node_index(&origin)
            .map_or_else(|| "unknown".to_string(), |idx| idx.to_string());
        debug!(
            "Received weak certificate {} (origin Node{}, round {}): replaying votes",
            header.id,
            origin_node,
            certificate.round()
        );

        if !self
            .processing
            .get(&header.round)
            .map_or_else(|| false, |x| x.contains(&header.id))
        {
            self.process_header(&header).await?;
        }

        for (author, signature) in certificate.votes {
            let vote = Vote {
                id: header.id.clone(),
                round: header.round,
                origin: header.author,
                author,
                signature,
            };
            self.process_vote(vote).await?;
        }

        Ok(())
    }

    #[async_recursion]
    async fn process_certificate(&mut self, certificate: Certificate) -> DagResult<()> {
        let origin = certificate.origin();
        let origin_node = self
            .node_index(&origin)
            .map_or_else(|| "unknown".to_string(), |idx| idx.to_string());
        debug!(
            "Received certificate {} (origin Node{}, round {}): entering processing pipeline",
            certificate.header.id,
            origin_node,
            certificate.round()
        );
        debug!("Processing {:?}", certificate);

        // Process the header embedded in the certificate if we haven't already voted for it (if we already
        // voted, it means we already processed it). Since this header got certified, we are sure that all
        // the data it refers to (ie. its payload and its parents) are available. We can thus continue the
        // processing of the certificate even if we don't have them in store right now.
        if !self
            .processing
            .get(&certificate.header.round)
            .map_or_else(|| false, |x| x.contains(&certificate.header.id))
        {
            // This function may still throw an error if the storage fails.
            self.process_header(&certificate.header).await?;
        }

        // Ensure we have all the ancestors of this certificate yet. If we don't, the synchronizer will gather
        // them and trigger re-processing of this certificate.
        if !self.synchronizer.deliver_certificate(&certificate).await? {
            debug!(
                "Certificate {} (round {}) suspended in synchronizer: missing ancestor certificates, will be retried by CertificateWaiter",
                certificate.header.id,
                certificate.round()
            );
            return Ok(());
        }

        // Store the certificate.
        let bytes = bincode::serialize(&certificate).expect("Failed to serialize certificate");
        self.store.write(certificate.digest().to_vec(), bytes).await;
        self.known_headers
            .insert(certificate.header.id.clone(), certificate.header.clone());
        self.certified_headers
            .insert(certificate.header.id.clone(), certificate.round());
        self.votes_aggregators.remove(&certificate.header.id);
        self.buffered_votes.remove(&certificate.header.id);
        let certificate_progress = self.record_certificate_delivery(&certificate);
        if certificate_progress {
            self.track_adaptive_wait_progress(certificate.round()).await;
        }

        // Aggregate certificates by their own round instead of a single global current_round.
        // Whichever round reaches the unlock condition first can be dispatched to proposer first.
        let target_round_start = certificate.round();
        let target_round_end = target_round_start + self.committee.solid_wave_length();
        for target_round in target_round_start..target_round_end {
            if let Some(parents) = self
                .certificates_aggregators
                .entry(target_round)
                .or_insert_with(|| Box::new(CertificatesAggregator::new(target_round)))
                .append(certificate.clone(), &self.committee)?
            {
                self.update_adaptive_wait_round(target_round, parents).await?;
            }
        }

        // Debug: resolve each solid_step_vertex in the merge to [round, node_id].
        // let current_round = target_round + 1;
        // if current_round % self.committee.solid_step_length() == 0 && current_round > 1 {
        //     if let Some(agg) = self.certificates_aggregators.get(&target_round) {
        //         if let Some(digests) = agg.last_solid_step_union_digests() {
        //             let mut vertices = Vec::with_capacity(digests.len());
        //             for digest in digests {
        //                 if let Ok(Some(bytes)) = self.store.read(digest.to_vec()).await {
        //                     if let Ok(cert) = bincode::deserialize::<Certificate>(&bytes) {
        //                         let node_id = self.node_index(&cert.origin()).unwrap_or(999);
        //                         vertices.push(format!("[{},{}]", cert.round(), node_id));
        //                         debug!(
        //                             "solid_step_vertex {} -> [{},{}]",
        //                             digest,
        //                             cert.round(),
        //                             node_id
        //                         );
        //                     }
        //                 }
        //             }
        //             if !vertices.is_empty() {
        //                 debug!(
        //                     "solid_step_union (round {}): {}",
        //                     current_round,
        //                     vertices.join(", ")
        //                 );
        //             }
        //         }
        //     }
        // }

        // Send it to the consensus layer.
        let id = certificate.header.id.clone();
        let origin = certificate.origin();
        let origin_node = self
            .node_index(&origin)
            .map_or_else(|| "unknown".to_string(), |idx| idx.to_string());
        debug!(
            "Delivered certificate {} (origin Node{}, round {}) to the consensus",
            id,
            origin_node,
            certificate.round()
        );
        if let Err(e) = self.tx_consensus.send(certificate).await {
            warn!(
                "Failed to deliver certificate {} to the consensus: {}",
                id, e
            );
        }
        Ok(())
    }

    fn sanitize_header(&mut self, header: &Header) -> DagResult<()> {
        ensure!(
            self.gc_round <= header.round,
            DagError::TooOld(header.id.clone(), header.round)
        );

        // Verify the header's signature.
        header.verify(&self.committee)?;

        // TODO [issue #3]: Prevent bad nodes from sending junk headers with high round numbers.

        Ok(())
    }

    fn sanitize_vote(&mut self, vote: &Vote) -> DagResult<()> {
        // ensure!(
        //     self.current_header.round >= vote.round,
        //     DagError::TooOld(vote.digest(), vote.round)
        // );

        // Ensure we receive a vote on the expected header.
        // ensure!(
        //     // vote.id == self.current_header.id
        //     //     && vote.origin == self.current_header.author
        //     //     && vote.round == self.current_header.round,
        //     vote.id == self.current_header.id
        //         && vote.origin == self.current_header.author,
        //     DagError::UnexpectedVote(vote.id.clone())
        // );

        // // Verify the vote.
        vote.verify(&self.committee).map_err(DagError::from)?;
        Ok(())
    }

    fn sanitize_certificate(&mut self, certificate: &Certificate) -> DagResult<()> {
        ensure!(
            self.gc_round <= certificate.round(),
            DagError::TooOld(certificate.digest(), certificate.round())
        );

        // Verify the certificate (and the embedded header).
        certificate.verify(&self.committee).map_err(DagError::from)
    }

    fn sanitize_sync_weak_certificate(&mut self, certificate: &Certificate) -> DagResult<()> {
        ensure!(
            self.gc_round <= certificate.round(),
            DagError::TooOld(certificate.digest(), certificate.round())
        );

        certificate
            .verify_with_threshold(&self.committee, self.committee.validity_threshold())
            .map_err(DagError::from)
    }

    // Main loop listening to incoming messages.
    pub async fn run(&mut self) {
        loop {
            let result = tokio::select! {
                // We receive here messages from other primaries.
                Some(message) = self.rx_primaries.recv() => {
                    match message {
                        PrimaryMessage::Header(header) => {
                            let origin_node = self
                                .node_index(&header.author)
                                .map_or_else(|| "unknown".to_string(), |idx| idx.to_string());
                            debug!(
                                "Channel recv header {} (origin Node{}, round {})",
                                header.id,
                                origin_node,
                                header.round
                            );
                            match self.sanitize_header(&header) {
                                Ok(()) => self.process_header(&header).await,
                                Err(e) => {
                                    debug!(
                                        "Discarding header {} (round {}) in sanitize_header: {}",
                                        header.id,
                                        header.round,
                                        e
                                    );
                                    Err(e)
                                }
                            }

                        },
                        PrimaryMessage::HeaderBundle(bundle) => {
                            let header = &bundle.header;
                            let origin_node = self
                                .node_index(&header.author)
                                .map_or_else(|| "unknown".to_string(), |idx| idx.to_string());
                            debug!(
                                "Channel recv header bundle {} (origin Node{}, round {}, bundled_parents={})",
                                header.id,
                                origin_node,
                                header.round,
                                bundle.parent_certificates.len()
                            );
                            self.process_header_bundle(bundle).await
                        },
                        PrimaryMessage::Vote(vote) => {
                            match self.sanitize_vote(&vote) {
                                Ok(()) => self.process_vote(vote).await,
                                Err(e) => {
                                    debug!(
                                        "Discarding vote for header {:?} in sanitize_vote: {}",
                                        vote.id,
                                        e
                                    );
                                    Err(e)
                                }
                            }
                        },
                        PrimaryMessage::Certificate(certificate) => {
                            let origin = certificate.origin();
                            let origin_node = self
                                .node_index(&origin)
                                .map_or_else(|| "unknown".to_string(), |idx| idx.to_string());
                            debug!(
                                "Channel recv certificate {} (origin Node{}, round {})",
                                certificate.header.id,
                                origin_node,
                                certificate.round()
                            );
                            match self.sanitize_certificate(&certificate) {
                                Ok(()) =>  self.process_certificate(certificate).await,
                                Err(e) => {
                                    debug!(
                                        "Discarding certificate {} (round {}) in sanitize_certificate: {}",
                                        certificate.header.id,
                                        certificate.round(),
                                        e
                                    );
                                    Err(e)
                                }
                            }
                        },
                        PrimaryMessage::SyncWeakCertificate(certificate) => {
                            let origin = certificate.origin();
                            let origin_node = self
                                .node_index(&origin)
                                .map_or_else(|| "unknown".to_string(), |idx| idx.to_string());
                            debug!(
                                "Channel recv weak certificate {} (origin Node{}, round {})",
                                certificate.header.id,
                                origin_node,
                                certificate.round()
                            );
                            match self.sanitize_sync_weak_certificate(&certificate) {
                                Ok(()) => self.process_sync_weak_certificate(certificate).await,
                                Err(e) => {
                                    debug!(
                                        "Discarding weak certificate {} (round {}) in sanitize_sync_weak_certificate: {}",
                                        certificate.header.id,
                                        certificate.round(),
                                        e
                                    );
                                    Err(e)
                                }
                            }
                        },
                        _ => panic!("Unexpected core message")
                    }
                },

                // We receive here loopback headers from the `HeaderWaiter`. Those are headers for which we interrupted
                // execution (we were missing some of their dependencies) and we are now ready to resume processing.
                Some(header) = self.rx_header_waiter.recv() => {
                    let origin_node = self
                        .node_index(&header.author)
                        .map_or_else(|| "unknown".to_string(), |idx| idx.to_string());
                    debug!(
                        "Channel recv header(waiter) {} (origin Node{}, round {})",
                        header.id,
                        origin_node,
                        header.round
                    );
                    self.process_header(&header).await
                },

                // We receive here loopback certificates from the `CertificateWaiter`. Those are certificates for which
                // we interrupted execution (we were missing some of their ancestors) and we are now ready to resume
                // processing.
                Some(certificate) = self.rx_certificate_waiter.recv() => {
                    let origin = certificate.origin();
                    let origin_node = self
                        .node_index(&origin)
                        .map_or_else(|| "unknown".to_string(), |idx| idx.to_string());
                    debug!(
                        "Channel recv certificate(waiter) {} (origin Node{}, round {})",
                        certificate.header.id,
                        origin_node,
                        certificate.round()
                    );
                    self.process_certificate(certificate).await
                },

                // We also receive here our new headers created by the `Proposer`.
                Some(header) = self.rx_proposer.recv() => self.process_own_header(header).await,
            };
            match result {
                Ok(()) => (),
                Err(DagError::StoreError(e)) => {
                    error!("{}", e);
                    panic!("Storage failure: killing node.");
                }
                Err(e @ DagError::TooOld(..)) => debug!("{}", e),
                Err(e) => warn!("{}", e),
            }
            self.flush_resolved_adaptive_wait_rounds().await;

            // Cleanup internal state.
            let round = self.consensus_round.load(Ordering::Relaxed);
            if round > self.gc_depth {
                let gc_round = round - self.gc_depth;
                self.last_voted.retain(|k, _| k >= &gc_round);
                self.processing.retain(|k, _| k >= &gc_round);
                self.certificates_aggregators.retain(|k, _| k >= &gc_round);
                self.vertex_round_states.retain(|k, _| k >= &gc_round);
                self.adaptive_wait_rounds.retain(|k, _| k >= &gc_round);
                self.adaptive_wait_released.retain(|k| *k >= gc_round);
                self.cancel_handlers.retain(|k, _| k >= &gc_round);
                self.pending_headers.retain(|_, h| h.round >= gc_round);
                let active_header_ids: HashSet<Digest> =
                    self.pending_headers.keys().cloned().collect();
                self.votes_aggregators
                    .retain(|digest, _| active_header_ids.contains(digest));
                self.gc_round = gc_round;
            }
        }
    }
}

// Copyright(C) Facebook, Inc. and its affiliates.
use crate::messages::{merge_author_bitmaps, Certificate, Header, ProposalParents};
use crate::primary::Round;
use config::{Committee, WorkerId};
use crypto::Hash as _;
use crypto::{Digest, PublicKey, SignatureService};
use log::debug;
#[cfg(feature = "benchmark")]
use log::info;
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use tokio::sync::mpsc::{Receiver, Sender};
use tokio::time::{sleep_until, Duration, Instant};

#[cfg(test)]
#[path = "tests/proposer_tests.rs"]
pub mod proposer_tests;

/// The proposer creates new headers and send them to the core for broadcasting and further processing.
pub struct Proposer {
    /// The public key of this primary.
    name: PublicKey,
    /// Node index for logging.
    node_id: Option<usize>,
    /// Service to sign headers.
    signature_service: SignatureService,
    /// The size of the headers' payload.
    header_size: usize,
    /// The maximum delay to wait for batches' digests.
    max_header_delay: u64,

    /// Receives the parents to include in the next header (along with their round number).
    rx_core: Receiver<(ProposalParents, Round)>,
    /// Receives the batches' digests from our workers.
    rx_workers: Receiver<(Digest, WorkerId)>,
    /// Sends newly created headers to the `Core`.
    tx_core: Sender<Header>,
    /// Number of local workers attached to this primary.
    local_workers: usize,
    /// Whether to only spill into the intermediate queue after the critical queue
    /// already accumulated a meaningful backlog.
    enable_adaptive_intermediate_spill: bool,
    /// Minimum number of critical digests required before adaptive spill starts.
    adaptive_intermediate_spill_trigger_digests: usize,
    /// Maximum number of digests to keep in the intermediate spill window.
    adaptive_intermediate_spill_cap_digests: usize,

    /// Unlocked proposal rounds waiting to be materialized into headers.
    unlocked_rounds: HashMap<Round, UnlockedRound>,
    /// Tracks which rounds this proposer has already materialized.
    proposed_rounds: HashSet<Round>,
    /// Monotonic unlock order used to preserve "first unlocked, first proposed".
    next_unlock_order: u64,
    /// Digests reserved for intermediate rounds.
    intermediate_digests: VecDeque<(Digest, WorkerId)>,
    /// Total size of digests reserved for intermediate rounds.
    intermediate_payload_size: usize,
    /// Digests reserved for critical rounds.
    critical_digests: VecDeque<(Digest, WorkerId)>,
    /// Total size of digests reserved for critical rounds.
    critical_payload_size: usize,
    /// The solid step length.
    solid_step_length: u64,
    /// The solid wave length.
    solid_wave_length: u64,
    /// Short grace period after parents become ready to absorb late certificates.
    parent_grace_delay: Duration,
}

struct UnlockedRound {
    parents: Vec<Digest>,
    solid_step_union: HashSet<Digest>,
    solid_wave_union: HashSet<Digest>,
    wave_back_link_target_round: Round,
    wave_back_link_author_bitmap: Vec<u8>,
    ready_since: Instant,
    unlock_order: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RoundClass {
    Bootstrap,
    Critical,
    Intermediate,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ProposalDecision {
    round: Round,
    include_payload: bool,
}

impl Proposer {
    #[allow(clippy::too_many_arguments)]
    pub fn spawn(
        name: PublicKey,
        committee: &Committee,
        signature_service: SignatureService,
        header_size: usize,
        max_header_delay: u64,
        enable_adaptive_intermediate_spill: bool,
        adaptive_intermediate_spill_trigger_digests: usize,
        adaptive_intermediate_spill_cap_digests: usize,
        rx_core: Receiver<(ProposalParents, Round)>,
        rx_workers: Receiver<(Digest, WorkerId)>,
        tx_core: Sender<Header>,
        _store: store::Store,
    ) {
        let node_id = committee
            .authorities
            .keys()
            .position(|authority| authority == &name);
        let genesis = Certificate::genesis(committee)
            .iter()
            .map(|x| x.digest())
            .collect();
        let solid_step_length = committee.solid_step_length() as u64;
        let solid_wave_length = committee.solid_wave_length() as u64;
        let local_workers = committee
            .authorities
            .get(&name)
            .map(|authority| authority.workers.len())
            .unwrap_or(1);
        // Disable the parent grace delay so newly unlocked rounds can be proposed immediately.
        let parent_grace_delay_ms = 0;

        let mut unlocked_rounds = HashMap::new();
        unlocked_rounds.insert(
            1,
            UnlockedRound {
                parents: genesis,
                solid_step_union: HashSet::new(),
                solid_wave_union: HashSet::new(),
                wave_back_link_target_round: 0,
                wave_back_link_author_bitmap: Vec::new(),
                ready_since: Instant::now(),
                unlock_order: 0,
            },
        );

        tokio::spawn(async move {
            Self {
                name,
                node_id,
                signature_service,
                header_size,
                max_header_delay,
                rx_core,
                rx_workers,
                tx_core,
                local_workers,
                enable_adaptive_intermediate_spill,
                adaptive_intermediate_spill_trigger_digests,
                adaptive_intermediate_spill_cap_digests,
                unlocked_rounds,
                proposed_rounds: HashSet::new(),
                next_unlock_order: 1,
                intermediate_digests: VecDeque::with_capacity(2 * header_size),
                intermediate_payload_size: 0,
                critical_digests: VecDeque::with_capacity(2 * header_size),
                critical_payload_size: 0,
                solid_step_length,
                solid_wave_length,
                parent_grace_delay: Duration::from_millis(parent_grace_delay_ms),
            }
            .run()
            .await;
        });
    }

    fn merge_parents(existing: &mut Vec<Digest>, parents: Vec<Digest>) -> (usize, usize) {
        let old_len = existing.len();
        let mut merged: HashSet<Digest> = existing.drain(..).collect();
        merged.extend(parents);
        let merged_len = merged.len();
        *existing = merged.into_iter().collect();
        (old_len, merged_len)
    }

    fn merge_unlocked_round(state: &mut UnlockedRound, update: ProposalParents) -> (usize, usize) {
        let solid_step_old_len = state.solid_step_union.len();
        let solid_wave_old_len = state.solid_wave_union.len();
        let (_old_len, _merged_len) = Self::merge_parents(&mut state.parents, update.parents);
        state.solid_step_union.extend(update.solid_step_union);
        state.solid_wave_union.extend(update.solid_wave_union);
        if state.wave_back_link_target_round == 0 {
            state.wave_back_link_target_round = update.wave_back_link_target_round;
        }
        if state.wave_back_link_target_round == update.wave_back_link_target_round {
            merge_author_bitmaps(
                &mut state.wave_back_link_author_bitmap,
                &update.wave_back_link_author_bitmap,
            );
        }
        (
            state.solid_step_union.len().saturating_sub(solid_step_old_len),
            state.solid_wave_union.len().saturating_sub(solid_wave_old_len),
        )
    }

    fn round_class(&self, round: Round) -> RoundClass {
        if round == 1 {
            RoundClass::Bootstrap
        } else if self.is_critical_round(round) {
            RoundClass::Critical
        } else {
            RoundClass::Intermediate
        }
    }

    fn is_critical_round(&self, round: Round) -> bool {
        round > 1 && (round - 1) % self.solid_step_length == 0
    }

    fn is_round_ready(&self, round: Round, state: &UnlockedRound) -> bool {
        round == 1 || state.ready_since.elapsed() >= self.parent_grace_delay
    }

    fn uses_intermediate_payload_queue(&self) -> bool {
        self.local_workers > 1 || self.enable_adaptive_intermediate_spill
    }

    fn should_spill_to_intermediate(&self) -> bool {
        self.enable_adaptive_intermediate_spill
            && self.critical_digests.len() >= self.adaptive_intermediate_spill_trigger_digests
            && self.intermediate_digests.len() < self.adaptive_intermediate_spill_cap_digests
    }

    fn next_recheck_deadline(
        &self,
        proposal_deadline: Instant,
        critical_payload_deadline: Option<Instant>,
        intermediate_payload_deadline: Option<Instant>,
    ) -> Instant {
        let mut next_deadline = std::iter::once(proposal_deadline)
            .chain(critical_payload_deadline.into_iter())
            .chain(
                self.uses_intermediate_payload_queue()
                    .then_some(intermediate_payload_deadline)
                    .into_iter()
                    .flatten(),
            )
            .min()
            .unwrap_or(proposal_deadline);

        for (round, state) in &self.unlocked_rounds {
            if self.proposed_rounds.contains(round) || state.parents.is_empty() {
                continue;
            }

            let ready_deadline = if *round == 1 {
                Instant::now()
            } else {
                state.ready_since + self.parent_grace_delay
            };
            if ready_deadline < next_deadline {
                next_deadline = ready_deadline;
            }
        }

        next_deadline
    }

    fn unlock_round(&mut self, round: Round, parent_update: ProposalParents) {
        if self.proposed_rounds.contains(&round) {
            debug!(
                "Received stale parents for already proposed round {}",
                round
            );
            return;
        }

        match self.unlocked_rounds.get_mut(&round) {
            Some(state) => {
                let old_len = state.parents.len();
                let (step_growth, wave_growth) = Self::merge_unlocked_round(state, parent_update);
                let merged_len = state.parents.len();
                debug!(
                    "Refreshing parents for unlocked round {} before proposal (old={}, merged={}, solid_step+={}, solid_wave+={})",
                    round, old_len, merged_len, step_growth, wave_growth
                );
            }
            None => {
                let unlock_order = self.next_unlock_order;
                self.next_unlock_order += 1;
                self.unlocked_rounds.insert(
                    round,
                    UnlockedRound {
                        parents: parent_update.parents,
                        solid_step_union: parent_update.solid_step_union,
                        solid_wave_union: parent_update.solid_wave_union,
                        wave_back_link_target_round: parent_update.wave_back_link_target_round,
                        wave_back_link_author_bitmap: parent_update.wave_back_link_author_bitmap,
                        ready_since: Instant::now(),
                        unlock_order,
                    },
                );
                debug!(
                    "Unlocked proposal round {} (unlock order {})",
                    round, unlock_order
                );
            }
        }
    }

    fn next_proposal_round(
        &self,
        proposal_timer_expired: bool,
        critical_payload_timer_expired: bool,
        critical_enough_digests: bool,
        intermediate_payload_timer_expired: bool,
        intermediate_enough_digests: bool,
    ) -> Option<ProposalDecision> {
        let critical_payload_trigger =
            critical_payload_timer_expired || critical_enough_digests;
        let intermediate_payload_trigger =
            intermediate_payload_timer_expired || intermediate_enough_digests;

        if let Some((round, _)) = self
            .unlocked_rounds
            .iter()
            .filter(|(round, state)| {
                !self.proposed_rounds.contains(round) && !state.parents.is_empty()
            })
            .filter(|(round, state)| {
                self.round_class(**round) == RoundClass::Bootstrap
                    && self.is_round_ready(**round, state)
            })
            .min_by_key(|(_, state)| state.unlock_order)
        {
            return Some(ProposalDecision {
                round: *round,
                include_payload: false,
            });
        }

        let critical_round = self
            .unlocked_rounds
            .iter()
            .filter(|(round, state)| {
                !self.proposed_rounds.contains(round)
                    && !state.parents.is_empty()
                    && self.round_class(**round) == RoundClass::Critical
                    && self.is_round_ready(**round, state)
            })
            .min_by_key(|(_, state)| state.unlock_order)
            .map(|(round, _)| *round);

        let intermediate_round = self
            .unlocked_rounds
            .iter()
            .filter(|(round, state)| {
                !self.proposed_rounds.contains(round)
                    && !state.parents.is_empty()
                    && self.round_class(**round) == RoundClass::Intermediate
                    && self.is_round_ready(**round, state)
            })
            .min_by_key(|(_, state)| state.unlock_order)
            .map(|(round, _)| *round);

        if !self.uses_intermediate_payload_queue() {
            let critical_has_payload = !self.critical_digests.is_empty();
            let critical_include_payload = critical_has_payload && critical_payload_trigger;
            let critical_eligible = critical_round.is_some()
                && (proposal_timer_expired || critical_payload_trigger);
            let intermediate_eligible = intermediate_round.is_some() && proposal_timer_expired;

            return match (critical_round, intermediate_round) {
                (Some(critical_round), Some(intermediate_round)) => {
                    if critical_include_payload {
                        Some(ProposalDecision {
                            round: critical_round,
                            include_payload: true,
                        })
                    } else if intermediate_eligible {
                        Some(ProposalDecision {
                            round: intermediate_round,
                            include_payload: false,
                        })
                    } else if critical_eligible {
                        Some(ProposalDecision {
                            round: critical_round,
                            include_payload: false,
                        })
                    } else {
                        None
                    }
                }
                (Some(critical_round), None) if critical_eligible => Some(ProposalDecision {
                    round: critical_round,
                    include_payload: critical_include_payload,
                }),
                (None, Some(intermediate_round)) if intermediate_eligible => {
                    Some(ProposalDecision {
                        round: intermediate_round,
                        include_payload: false,
                    })
                }
                _ => None,
            };
        }

        let critical_has_payload = !self.critical_digests.is_empty();
        let intermediate_has_payload = !self.intermediate_digests.is_empty();
        let critical_include_payload = critical_has_payload && critical_payload_trigger;
        let intermediate_include_payload =
            intermediate_has_payload && intermediate_payload_trigger;
        let critical_eligible = critical_round.is_some()
            && (proposal_timer_expired || critical_payload_trigger);
        let intermediate_eligible = intermediate_round.is_some()
            && (proposal_timer_expired || intermediate_payload_trigger);

        match (critical_round, intermediate_round) {
            (Some(critical_round), Some(intermediate_round)) => {
                if critical_include_payload {
                    Some(ProposalDecision {
                        round: critical_round,
                        include_payload: true,
                    })
                } else if intermediate_eligible {
                    Some(ProposalDecision {
                        round: intermediate_round,
                        include_payload: intermediate_include_payload,
                    })
                } else if critical_eligible {
                    Some(ProposalDecision {
                        round: critical_round,
                        include_payload: false,
                    })
                } else {
                    None
                }
            }
            (Some(critical_round), None) if critical_eligible => Some(ProposalDecision {
                round: critical_round,
                include_payload: critical_include_payload,
            }),
            (None, Some(intermediate_round)) if intermediate_eligible => Some(ProposalDecision {
                round: intermediate_round,
                include_payload: intermediate_include_payload,
            }),
            _ => None,
        }
    }

    fn payload_queue_for_worker(&self, worker_id: WorkerId) -> RoundClass {
        if !self.uses_intermediate_payload_queue() {
            RoundClass::Critical
        } else if self.enable_adaptive_intermediate_spill {
            if self.should_spill_to_intermediate() {
                RoundClass::Intermediate
            } else {
                RoundClass::Critical
            }
        } else if worker_id % 2 == 0 {
            RoundClass::Intermediate
        } else {
            RoundClass::Critical
        }
    }

    fn take_payload_for_round_class(
        &mut self,
        round_class: RoundClass,
    ) -> BTreeMap<Digest, WorkerId> {
        match round_class {
            RoundClass::Critical => {
                self.critical_payload_size = 0;
                self.critical_digests.drain(..).collect()
            }
            RoundClass::Intermediate => {
                self.intermediate_payload_size = 0;
                self.intermediate_digests.drain(..).collect()
            }
            RoundClass::Bootstrap => BTreeMap::new(),
        }
    }

    async fn make_header(
        &mut self,
        round: Round,
        unlocked_round: UnlockedRound,
        include_payload: bool,
    ) {
        // Make a new header.
        let payload = if include_payload {
            self.take_payload_for_round_class(self.round_class(round))
        } else {
            BTreeMap::new()
        };
        let mut header = Header::new(
            self.name,
            round,
            payload,
            unlocked_round.parents.iter().cloned().collect(),
            &mut self.signature_service,
        )
        .await;
        let origin_node = self
            .node_id
            .map_or_else(|| "unknown".to_string(), |idx| idx.to_string());
        debug!(
            "Created {:?} header {} (origin Node{}, round {}, payload_entries={})",
            self.round_class(round),
            header.id,
            origin_node,
            header.round,
            header.payload.len()
        );
        debug!("Created {:?}", header);

        // Maintain solid_step / solid_wave metadata according to the intended semantics:
        // - round 1: vertices = parents, merged = parents
        // - end rounds (r % len == 0): vertices = union(parent.merged), merged = {header}
        // - all other rounds: vertices = merged = union(parent.merged)
        debug!("the number of the parents is {}", header.parents.len());

        let is_solid_step_boundary =
            round == 1 || (round > 1 && (round - 1) % self.solid_step_length == 0);
        let is_solid_wave_boundary =
            round == 1 || (round > 1 && (round - 1) % self.solid_wave_length == 0);
        if round == 1 {
            let parent_set: HashSet<Digest> = unlocked_round.parents.into_iter().collect();
            header.store_solid_step_vertex(parent_set.clone());
            header.store_solid_step_merged_vertices(parent_set.clone());
            header.store_solid_wave_vertex(parent_set.clone());
            header.store_solid_wave_merged_vertices(parent_set);
        } else if is_solid_step_boundary {
            header.store_solid_step_vertex(unlocked_round.solid_step_union);

            let mut self_only: HashSet<Digest> = HashSet::new();
            self_only.insert(header.id.clone());
            header.store_solid_step_merged_vertices(self_only);
        } else {
            header.store_solid_step_vertex(unlocked_round.solid_step_union.clone());
            header.store_solid_step_merged_vertices(unlocked_round.solid_step_union);
        }
        if is_solid_wave_boundary {
            header.store_solid_wave_vertex(unlocked_round.solid_wave_union);

            let mut self_only: HashSet<Digest> = HashSet::new();
            self_only.insert(header.id.clone());
            header.store_solid_wave_merged_vertices(self_only);
        } else {
            header.store_solid_wave_vertex(unlocked_round.solid_wave_union.clone());
            header.store_solid_wave_merged_vertices(unlocked_round.solid_wave_union);
        }
        header.store_wave_back_link_summary(
            unlocked_round.wave_back_link_target_round,
            unlocked_round.wave_back_link_author_bitmap,
        );
        debug!(
            "Current round: {}, solid_step_vertices={}, solid_wave_vertices={}",
            round,
            header.solid_step_vertices.len(),
            header.solid_wave_vertices.len()
        );

        #[cfg(feature = "benchmark")]
        {
            let (payload_bytes, tusk_metadata_bytes, manta_extra_metadata_bytes, full_header_bytes) =
                header.serialized_size_breakdown();
            info!(
                "HEADER_SIZE round={} payload_bytes={} tusk_metadata_bytes={} \
                 manta_extra_metadata_bytes={} full_header_bytes={}",
                header.round,
                payload_bytes,
                tusk_metadata_bytes,
                manta_extra_metadata_bytes,
                full_header_bytes,
            );
        }

        #[cfg(feature = "benchmark")]
        for digest in header.payload.keys() {
            // NOTE: This log entry is used to compute performance.
            info!("Created {} -> {:?}", header, digest);
        }

        // Send the new header to the `Core` that will broadcast and process it.
        self.tx_core
            .send(header)
            .await
            .expect("Failed to send header");
    }

    // Main loop listening to incoming messages.
    pub async fn run(&mut self) {
        debug!("Dag starting with bootstrap round 1 unlocked");

        let mut proposal_deadline = Instant::now() + Duration::from_millis(self.max_header_delay);
        let mut critical_payload_deadline: Option<Instant> = None;
        let mut intermediate_payload_deadline: Option<Instant> = None;
        let timer = sleep_until(proposal_deadline);
        tokio::pin!(timer);

        loop {
            let now = Instant::now();
            let proposal_timer_expired = now >= proposal_deadline;
            let critical_enough_digests = self.critical_payload_size >= self.header_size;
            let intermediate_enough_digests = self.uses_intermediate_payload_queue()
                && self.intermediate_payload_size >= self.header_size;
            let critical_payload_timer_expired =
                critical_payload_deadline.is_some_and(|deadline| now >= deadline);
            let intermediate_payload_timer_expired = self.uses_intermediate_payload_queue()
                && intermediate_payload_deadline.is_some_and(|deadline| now >= deadline);

            if let Some(decision) = self.next_proposal_round(
                proposal_timer_expired,
                critical_payload_timer_expired,
                critical_enough_digests,
                intermediate_payload_timer_expired,
                intermediate_enough_digests,
            ) {
                if decision.round != 1 {
                    debug!(
                        "Proposing {:?} round {} (payload={}, proposal_timer_expired={}, critical_payload_timer_expired={}, critical_enough_digests={}, intermediate_payload_timer_expired={}, intermediate_enough_digests={})",
                        self.round_class(decision.round),
                        decision.round,
                        decision.include_payload,
                        proposal_timer_expired,
                        critical_payload_timer_expired,
                        critical_enough_digests,
                        intermediate_payload_timer_expired,
                        intermediate_enough_digests
                    );
                }

                let selected_class = self.round_class(decision.round);
                let proposal_state = self
                    .unlocked_rounds
                    .remove(&decision.round)
                    .expect("Unlocked round disappeared unexpectedly");
                self.make_header(decision.round, proposal_state, decision.include_payload)
                    .await;
                self.proposed_rounds.insert(decision.round);
                if decision.include_payload {
                    match selected_class {
                        RoundClass::Critical => critical_payload_deadline = None,
                        RoundClass::Intermediate => intermediate_payload_deadline = None,
                        RoundClass::Bootstrap => {}
                    }
                }
                proposal_deadline = Instant::now() + Duration::from_millis(self.max_header_delay);

                continue;
            }

            let next_deadline = self.next_recheck_deadline(
                proposal_deadline,
                critical_payload_deadline,
                intermediate_payload_deadline,
            );
            timer.as_mut().reset(next_deadline);

            tokio::select! {
                Some((parent_update, round)) = self.rx_core.recv() => {
                    let proposal_round = round + 1;
                    self.unlock_round(proposal_round, parent_update);
                }
                Some((digest, worker_id)) = self.rx_workers.recv() => {
                    let queue = self.payload_queue_for_worker(worker_id);
                    let payload_deadline =
                        Instant::now() + Duration::from_millis(self.max_header_delay);
                    match queue {
                        RoundClass::Critical => {
                            self.critical_payload_size += digest.size();
                            self.critical_digests.push_back((digest, worker_id));
                            critical_payload_deadline.get_or_insert(payload_deadline);
                        }
                        RoundClass::Intermediate => {
                            if self.uses_intermediate_payload_queue() {
                                self.intermediate_payload_size += digest.size();
                                self.intermediate_digests.push_back((digest, worker_id));
                                intermediate_payload_deadline.get_or_insert(payload_deadline);
                            } else {
                                debug!(
                                    "Ignoring unexpected intermediate payload assignment in single-worker mode"
                                );
                                self.critical_payload_size += digest.size();
                                self.critical_digests.push_back((digest, worker_id));
                                critical_payload_deadline.get_or_insert(payload_deadline);
                            }
                        }
                        RoundClass::Bootstrap => {}
                    }
                }
                () = &mut timer => {
                    // Nothing to do.
                }
            }
        }
    }
}

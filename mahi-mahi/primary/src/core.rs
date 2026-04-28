// Copyright(C) Facebook, Inc. and its affiliates.
use crate::aggregators::HeadersAggregator;
use crate::error::{DagError, DagResult};
use crate::messages::{Header, ProposalParents};
use crate::primary::{PrimaryMessage, Round};
use crate::synchronizer::Synchronizer;
use bytes::Bytes;
use config::Committee;
use crypto::{Digest, PublicKey};
use log::{debug, error, warn};
use network::SimpleSender;
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use store::Store;
use tokio::sync::mpsc::{Receiver, Sender};

#[cfg(test)]
#[path = "tests/core_tests.rs"]
pub mod core_tests;

pub struct Core {
    /// The public key of this primary.
    name: PublicKey,
    /// The committee information.
    committee: Committee,
    /// The persistent storage.
    store: Store,
    /// Handles synchronization with other nodes and our workers.
    synchronizer: Synchronizer,
    /// The current consensus round (used for cleanup).
    consensus_round: Arc<AtomicU64>,
    /// The depth of the garbage collector.
    gc_depth: Round,

    /// Receiver for dag messages (headers).
    rx_primaries: Receiver<PrimaryMessage>,
    /// Receives loopback headers from the `HeaderWaiter`.
    rx_header_waiter: Receiver<Header>,
    /// Receives our newly created headers from the `Proposer`.
    rx_proposer: Receiver<Header>,
    /// Output all headers to the next layer.
    tx_consensus: Sender<Header>,
    /// Send valid parent snapshots to the `Proposer` (along with their round).
    tx_proposer: Sender<(ProposalParents, Round)>,

    /// The last garbage collected round.
    gc_round: Round,
    /// Headers already fully processed and stored, keyed by round.
    processed_headers: HashMap<Round, HashSet<Digest>>,
    /// Aggregates headers to use as parents for new headers.
    headers_aggregators: HashMap<Round, Box<HeadersAggregator>>,
    /// Best-effort sender for header broadcast.
    network: SimpleSender,
}

impl Core {
    async fn store_header_for_sync(&mut self, header: &Header) {
        let bytes = bincode::serialize(header).expect("Failed to serialize header");
        self.store.write(header.id.to_vec(), bytes).await;
    }

    fn node_index(&self, key: &PublicKey) -> Option<usize> {
        self.committee
            .authorities
            .keys()
            .position(|authority| authority == key)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn spawn(
        name: PublicKey,
        committee: Committee,
        store: Store,
        synchronizer: Synchronizer,
        consensus_round: Arc<AtomicU64>,
        gc_depth: Round,
        rx_primaries: Receiver<PrimaryMessage>,
        rx_header_waiter: Receiver<Header>,
        rx_proposer: Receiver<Header>,
        tx_consensus: Sender<Header>,
        tx_proposer: Sender<(ProposalParents, Round)>,
    ) {
        tokio::spawn(async move {
            Self {
                name,
                committee,
                store,
                synchronizer,
                consensus_round,
                gc_depth,
                rx_primaries,
                rx_header_waiter,
                rx_proposer,
                tx_consensus,
                tx_proposer,
                gc_round: 0,
                processed_headers: HashMap::with_capacity(2 * gc_depth as usize),
                headers_aggregators: HashMap::with_capacity(2 * gc_depth as usize),
                network: SimpleSender::new(),
            }
            .run()
            .await;
        });
    }

    async fn process_own_header(&mut self, header: Header) -> DagResult<()> {
        debug!(
            "Broadcasting header {} (round {}) to other primaries",
            header.id, header.round
        );

        let addresses: Vec<_> = self
            .committee
            .others_primaries(&self.name)
            .iter()
            .map(|(_, x)| x.primary_to_primary)
            .collect();
        let bytes = bincode::serialize(&PrimaryMessage::Header(header.clone()))
            .expect("Failed to serialize our own header");
        self.network.broadcast(addresses, Bytes::from(bytes)).await;

        self.process_header(&header).await
    }

    async fn process_header(&mut self, header: &Header) -> DagResult<()> {
        if self
            .processed_headers
            .get(&header.round)
            .map_or(false, |headers| headers.contains(&header.id))
        {
            debug!(
                "Ignoring already processed header {} (round {})",
                header.id, header.round
            );
            return Ok(());
        }

        let origin_node = self
            .node_index(&header.author)
            .map_or_else(|| "unknown".to_string(), |idx| idx.to_string());
        debug!(
            "Received header {} (origin Node{}, round {}): entering processing pipeline",
            header.id, origin_node, header.round
        );
        debug!("Processing {:?}", header);

        // Ensure we have the parents. If at least one parent is missing, the synchronizer returns an empty
        // vector and re-schedules processing of this header once the missing headers arrive.
        let parents = self.synchronizer.get_parents(header).await?;
        if !header.parents.is_empty() && parents.is_empty() {
            debug!(
                "Header {} (round {}) suspended in synchronizer: missing parent(s), will be retried by HeaderWaiter",
                header.id,
                header.round
            );
            return Ok(());
        }

        // Check the parent headers. We allow commit-time weak edges from the whole solid-wave
        // window, but only the solid-step window contributes to processing.
        let round = header.round;
        let solid_step_length = self.committee.solid_step_length();
        let solid_wave_length = self.committee.solid_wave_length();
        let is_solid_step = self.committee.is_solid_step(round);
        let step_index: Round = ((round - 1) % solid_step_length) + 1;
        let wave_index: Round = ((round - 1) % solid_wave_length) + 1;
        let regular_weak_start: Round = round.saturating_sub(step_index);
        let commit_weak_start: Round = round.saturating_sub(wave_index);

        let mut stake = 0u64;
        let mut solid_step_union = HashSet::new();

        for parent in &parents {
            ensure!(
                parent.round >= commit_weak_start && parent.round < round,
                DagError::MalformedHeader(header.id.clone())
            );
            if parent.round >= regular_weak_start {
                stake += self.committee.stake(&parent.author) as u64;
                if is_solid_step {
                    solid_step_union.extend(parent.solid_step_vertices_merged.iter().cloned());
                }
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

        // Make the header discoverable to parent sync as soon as its structural checks pass.
        // This prevents one missing payload from causing an artificial "missing parent" cascade
        // for all descendants that already reference this header.
        self.store_header_for_sync(header).await;

        // Ensure we have the payload. If we don't, the synchronizer will ask our workers to get it,
        // and then re-schedule processing of this header once we have it.
        if self.synchronizer.missing_payload(header).await? {
            debug!(
                "Header {} (round {}) suspended in synchronizer: missing payload, will be retried by HeaderWaiter",
                header.id,
                header.round
            );
            return Ok(());
        }

        self.processed_headers
            .entry(header.round)
            .or_insert_with(HashSet::new)
            .insert(header.id.clone());

        // Aggregate headers by their own round instead of a single global current round.
        // Whichever round reaches the unlock condition first can be dispatched to proposer first.
        let target_round_start = header.round;
        let target_round_end = target_round_start + self.committee.solid_wave_length();
        for target_round in target_round_start..target_round_end {
            if let Some(parents) = self
                .headers_aggregators
                .entry(target_round)
                .or_insert_with(|| Box::new(HeadersAggregator::new(target_round)))
                .append(header.clone(), &self.committee)?
            {
                self.tx_proposer
                    .send((parents, target_round))
                    .await
                    .expect("Failed to send header parents to proposer");
            }
        }

        debug!(
            "Delivered header {} (origin Node{}, round {}) to the next layer",
            header.id, origin_node, header.round
        );
        if let Err(e) = self.tx_consensus.send(header.clone()).await {
            warn!(
                "Failed to deliver header {} to the next layer: {}",
                header.id, e
            );
        }
        Ok(())
    }

    fn sanitize_header(&mut self, header: &Header) -> DagResult<()> {
        ensure!(
            self.gc_round <= header.round,
            DagError::TooOld(header.id.clone(), header.round)
        );

        header.verify(&self.committee)?;

        // TODO [issue #3]: Prevent bad nodes from sending junk headers with high round numbers.
        Ok(())
    }

    pub async fn run(&mut self) {
        loop {
            let result = tokio::select! {
                // We receive here headers from other primaries.
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
                        }
                        PrimaryMessage::HeadersRequest(..) => {
                            panic!("Header requests should be handled by Helper")
                        }
                    }
                },

                // Loopback headers from the `HeaderWaiter`.
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

                // Newly created headers from the `Proposer`.
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

            let round = self.consensus_round.load(Ordering::Relaxed);
            if round > self.gc_depth {
                let gc_round = round - self.gc_depth;
                self.processed_headers.retain(|k, _| k >= &gc_round);
                self.headers_aggregators.retain(|k, _| k >= &gc_round);
                self.gc_round = gc_round;
            }
        }
    }
}

// Copyright(C) Facebook, Inc. and its affiliates.
use crate::aggregators::{CertificatesAggregator, VotesAggregator};
use crate::error::{DagError, DagResult};
use crate::messages::{author_bitmap_stake, merge_author_bitmaps, set_author_bit, Certificate, Header, ProposalParents, Vote};
use crate::primary::{PrimaryMessage, Round};
use crate::synchronizer::Synchronizer;
use async_recursion::async_recursion;
use bytes::Bytes;
use config::Committee;
use crypto::Hash as _;
use crypto::{Digest, PublicKey, SignatureService};
use log::{debug, error, warn};
use network::{CancelHandler, ReliableSender};
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
    /// Service to sign headers.
    signature_service: SignatureService,
    /// The current consensus round (used for cleanup).
    consensus_round: Arc<AtomicU64>,
    /// The depth of the garbage collector.
    gc_depth: Round,

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
    /// All known uncertified headers waiting for a local vote quorum.
    pending_headers: HashMap<Digest, Header>,
    /// One vote aggregator per known uncertified header.
    votes_aggregators: HashMap<Digest, VotesAggregator>,
    /// Votes that arrived before we had processed the corresponding header.
    pending_votes: HashMap<Digest, HashMap<PublicKey, Vote>>,
    /// Headers for which we have already seen a certificate and should stop aggregating votes.
    sealed_headers: HashMap<Digest, Round>,
    /// Certificates fully processed by this node.
    processed_certificates: HashMap<Digest, Round>,
    /// Aggregates certificates to use as parents for new headers.
    certificates_aggregators: HashMap<Round, Box<CertificatesAggregator>>,
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

    fn wave_back_link_summary(&self, parents: &[Certificate], round: Round) -> (Round, Vec<u8>) {
        let Some(target_round) = self.committee.wave_back_link_tracking_round(round) else {
            return (0, Vec::new());
        };

        let mut bitmap = vec![0; self.committee.authority_bitmap_len()];
        for parent in parents {
            if parent.round() == target_round {
                if let Some(index) = self.committee.authority_index(&parent.origin()) {
                    set_author_bit(&mut bitmap, index);
                }
            }
            if parent.header.wave_back_link_target_round == target_round {
                merge_author_bitmaps(&mut bitmap, &parent.header.wave_back_link_author_bitmap);
            }
        }

        (target_round, bitmap)
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
                rx_primaries,
                rx_header_waiter,
                rx_certificate_waiter,
                rx_proposer,
                tx_consensus,
                tx_proposer,
                gc_round: 0,
                last_voted: HashMap::with_capacity(2 * gc_depth as usize),
                processing: HashMap::with_capacity(2 * gc_depth as usize),
                pending_headers: HashMap::with_capacity(2 * gc_depth as usize),
                votes_aggregators: HashMap::with_capacity(2 * gc_depth as usize),
                pending_votes: HashMap::with_capacity(2 * gc_depth as usize),
                sealed_headers: HashMap::with_capacity(2 * gc_depth as usize),
                processed_certificates: HashMap::with_capacity(2 * gc_depth as usize),
                certificates_aggregators: HashMap::with_capacity(2 * gc_depth as usize),
                network: ReliableSender::new(),
                cancel_handlers: HashMap::with_capacity(2 * gc_depth as usize),
            }
            .run()
            .await;
        });
    }

    async fn process_own_header(&mut self, header: Header) -> DagResult<()> {
        // Track this locally proposed header immediately so votes that race ahead of the
        // local processing path can still be aggregated into a certificate.
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
        let addresses: Vec<_> = self
            .committee
            .others_primaries(&self.name)
            .iter()
            .map(|(_, x)| x.primary_to_primary)
            .collect();
        let bytes = bincode::serialize(&PrimaryMessage::Header(header.clone()))
            .expect("Failed to serialize our own header");
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

        // Check the parent certificates. Weak parents are always allowed inside
        // the current solid step; optionally they may extend into earlier solid
        // steps that still lie inside the current solid-wave window.
        let round = header.round as u64;
        let is_solid_step = self.committee.is_solid_step(round);
        let regular_weak_start = self.committee.solid_step_parent_start(round);
        let cross_step_weak_start = self.committee.cross_step_weak_parent_start(round);

        let mut stake = 0u64;
        let mut solid_step_union = HashSet::new();

        for x in &parents {
            ensure!(
                x.round() >= cross_step_weak_start && x.round() < round,
                DagError::MalformedHeader(header.id.clone())
            );
            if x.round() >= regular_weak_start {
                stake += self.committee.stake(&x.origin()) as u64;
                if is_solid_step {
                    solid_step_union.extend(x.header.solid_step_vertices_merged.iter().cloned());
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

        let (expected_back_link_round, expected_back_link_bitmap) =
            self.wave_back_link_summary(&parents, round);
        ensure!(
            header.wave_back_link_target_round == expected_back_link_round
                && header.wave_back_link_author_bitmap == expected_back_link_bitmap,
            DagError::MalformedHeader(header.id.clone())
        );

        if let Some(target_round) = self.committee.wave_back_link_target_round(round) {
            let link_stake = author_bitmap_stake(&self.committee, &expected_back_link_bitmap);
            ensure!(
                link_stake >= self.committee.quorum_threshold(),
                DagError::HeaderRequiresWaveLink(header.id.clone(), target_round)
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

        let header_id = header.id.clone();
        let header_is_sealed = self.sealed_headers.contains_key(&header_id);
        if !header_is_sealed {
            self.pending_headers
                .entry(header_id.clone())
                .or_insert_with(|| header.clone());
            self.votes_aggregators
                .entry(header_id.clone())
                .or_insert_with(VotesAggregator::new);

            if let Some(cached_votes) = self.pending_votes.remove(&header_id) {
                for vote in cached_votes.into_values() {
                    self.process_vote(vote).await?;
                }
            }
        }

        if self.sealed_headers.contains_key(&header_id) {
            debug!(
                "Skipping local vote for header {} (round {}): certificate already available",
                header.id, header.round
            );
            return Ok(());
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

            // Make a vote, process it locally, and fan it out to all other primaries so that
            // any of them can assemble the certificate once they collect a quorum.
            let vote = Vote::new(header, &self.name, &mut self.signature_service).await;
            debug!(
                "Created vote {:?} for header {} (round {})",
                vote, header.id, header.round
            );
            self.process_vote(vote.clone())
                .await
                .expect("Failed to process our own vote");

            let bytes = bincode::serialize(&PrimaryMessage::Vote(vote))
                .expect("Failed to serialize our own vote");
            let addresses: Vec<_> = self
                .committee
                .others_primaries(&self.name)
                .iter()
                .map(|(_, x)| x.primary_to_primary)
                .collect();
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
        if self.sealed_headers.contains_key(&vote_id) {
            debug!(
                "Ignoring vote for header {} (round {}): certificate already available",
                vote_id, vote.round
            );
            return Ok(());
        }

        let header = match self.pending_headers.get(&vote_id) {
            Some(header) => header.clone(),
            None => {
                let vote_round = vote.round;
                let author = vote.author;
                self.pending_votes
                    .entry(vote_id.clone())
                    .or_insert_with(HashMap::new)
                    .entry(author)
                    .or_insert(vote);
                debug!(
                    "Caching vote for header {} (round {}) until the header is available",
                    vote_id, vote_round
                );
                return Ok(());
            }
        };

        // Add it to the votes' aggregator and try to make a new certificate.
        let aggregator = self
            .votes_aggregators
            .entry(vote_id.clone())
            .or_insert_with(VotesAggregator::new);
        if let Some(certificate) = aggregator.append(vote, &self.committee, &header)? {
            self.sealed_headers.insert(vote_id.clone(), header.round);
            self.pending_headers.remove(&vote_id);
            self.votes_aggregators.remove(&vote_id);
            self.pending_votes.remove(&vote_id);
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

            if certificate.origin() == self.name {
                // Only the header author broadcasts the certificate. Other primaries may still
                // assemble it locally and use it immediately, but they avoid duplicate network
                // broadcasts for the same certificate.
                let cert_id = certificate.header.id.clone();
                let cert_round = certificate.round();
                debug!(
                    "Broadcasting certificate {} (round {}) to other primaries",
                    cert_id, cert_round
                );
                let addresses: Vec<_> = self
                    .committee
                    .others_primaries(&self.name)
                    .iter()
                    .map(|(_, x)| x.primary_to_primary)
                    .collect();
                let bytes = bincode::serialize(&PrimaryMessage::Certificate(certificate.clone()))
                    .expect("Failed to serialize our own certificate");
                for address in addresses {
                    let handler = self.network.send(address, Bytes::from(bytes.clone())).await;
                    let id = cert_id.clone();
                    tokio::spawn(async move {
                        match handler.await {
                            Ok(_) => {
                                debug!(
                                    "Certificate {} (round {}) successfully delivered to primary {}",
                                    id, cert_round, address
                                );
                            }
                            Err(_) => {
                                debug!(
                                    "Certificate {} (round {}) delivery to primary {} was canceled or failed",
                                    id, cert_round, address
                                );
                            }
                        }
                    });
                }
            } else {
                debug!(
                    "Keeping certificate {} (round {}) local: author {} will broadcast it",
                    certificate.header.id,
                    certificate.round(),
                    certificate.origin(),
                );
            }

            // Process the new certificate.
            self.process_certificate(certificate)
                .await
                .expect("Failed to process valid certificate");
        }
        Ok(())
    }

    #[async_recursion]
    async fn process_certificate(&mut self, certificate: Certificate) -> DagResult<()> {
        let certificate_digest = certificate.digest();
        if self
            .processed_certificates
            .contains_key(&certificate.header.id)
            || self
                .store
                .read(certificate_digest.to_vec())
                .await?
                .is_some()
        {
            debug!(
                "Skipping already processed certificate {} (round {})",
                certificate.header.id,
                certificate.round()
            );
            return Ok(());
        }

        self.sealed_headers
            .insert(certificate.header.id.clone(), certificate.round());
        self.pending_headers.remove(&certificate.header.id);
        self.votes_aggregators.remove(&certificate.header.id);
        self.pending_votes.remove(&certificate.header.id);

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
        self.store.write(certificate_digest.to_vec(), bytes).await;
        self.processed_certificates
            .insert(certificate.header.id.clone(), certificate.round());

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
                let proposal_round = target_round + 1;
                if let Some(back_link_round) = self.committee.wave_back_link_target_round(proposal_round) {
                    if parents.wave_back_link_target_round != back_link_round {
                        debug!(
                            "Delaying proposer unlock for round {}: parent bitmap tracks round {} instead of {}",
                            proposal_round,
                            parents.wave_back_link_target_round,
                            back_link_round,
                        );
                        continue;
                    }
                    let link_stake = author_bitmap_stake(
                        &self.committee,
                        &parents.wave_back_link_author_bitmap,
                    );
                    if link_stake < self.committee.quorum_threshold() {
                        debug!(
                            "Delaying proposer unlock for round {}: only {} stake links to round {} (need {})",
                            proposal_round,
                            link_stake,
                            back_link_round,
                            self.committee.quorum_threshold(),
                        );
                        continue;
                    }
                }

                // Send it to the `Proposer`.
                self.tx_proposer
                    .send((parents, target_round))
                    .await
                    .expect("Failed to send certificate");
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
        vote.verify(&self.committee).map_err(DagError::from)
    }

    fn sanitize_certificate(&mut self, certificate: &Certificate) -> DagResult<()> {
        ensure!(
            self.gc_round <= certificate.round(),
            DagError::TooOld(certificate.digest(), certificate.round())
        );

        // Verify the certificate (and the embedded header).
        certificate.verify(&self.committee).map_err(DagError::from)
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

            // Cleanup internal state.
            let round = self.consensus_round.load(Ordering::Relaxed);
            if round > self.gc_depth {
                let gc_round = round - self.gc_depth;
                self.last_voted.retain(|k, _| k >= &gc_round);
                self.processing.retain(|k, _| k >= &gc_round);
                self.certificates_aggregators.retain(|k, _| k >= &gc_round);
                self.cancel_handlers.retain(|k, _| k >= &gc_round);
                self.pending_headers.retain(|_, h| h.round >= gc_round);
                self.pending_votes.retain(|_, votes| {
                    votes.values().next().map_or(false, |vote| vote.round >= gc_round)
                });
                self.sealed_headers.retain(|_, round| *round >= gc_round);
                self.processed_certificates
                    .retain(|_, round| *round >= gc_round);
                let active_header_ids: HashSet<Digest> =
                    self.pending_headers.keys().cloned().collect();
                self.votes_aggregators
                    .retain(|digest, _| active_header_ids.contains(digest));
                self.gc_round = gc_round;
            }
        }
    }
}

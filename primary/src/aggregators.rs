// Copyright(C) Facebook, Inc. and its affiliates.
use crate::error::DagResult;
use crate::messages::{Header, ProposalParents};
use crate::primary::Round;
use config::{Committee, Stake};
use crypto::{Digest, PublicKey};
use log::debug;
use std::collections::HashSet;

/// Aggregate headers and check if we reach a quorum.
pub struct HeadersAggregator {
    expected_round: Round,
    weight: Stake,
    seen: HashSet<Digest>,
    headers: Vec<Digest>,
    used: HashSet<PublicKey>,
    /// Incremental union of parents' solid-step summaries for the proposal round.
    solid_step_union: HashSet<Digest>,
    /// Incremental union of parents' solid-wave summaries for the proposal round.
    solid_wave_union: HashSet<Digest>,
    /// Last computed union of parents' solid_step_vertices_merged on solid rounds
    /// (for debug / final_dag display).
    last_union_set: Option<Vec<Digest>>,
}

impl HeadersAggregator {
    pub fn new(expected_round: Round) -> Self {
        Self {
            expected_round,
            weight: 0,
            seen: HashSet::new(),
            headers: Vec::new(),
            used: HashSet::new(),
            solid_step_union: HashSet::new(),
            solid_wave_union: HashSet::new(),
            last_union_set: None,
        }
    }

    /// Returns the last computed union of parents' solid_step_vertices_merged
    /// (when advancing to a solid round). Used by core to resolve digests to
    /// [round, node_id] for debug and final_dag.
    pub fn last_solid_step_union_digests(&self) -> Option<&[Digest]> {
        self.last_union_set.as_deref()
    }

    fn extend_step_union(&mut self, header: &Header) {
        if header.solid_step_vertices_merged.is_empty() {
            self.solid_step_union
                .extend(header.solid_step_vertices.iter().cloned());
        } else {
            self.solid_step_union
                .extend(header.solid_step_vertices_merged.iter().cloned());
        }
    }

    fn extend_wave_union(&mut self, header: &Header) {
        if header.solid_wave_vertices_merged.is_empty() {
            self.solid_wave_union
                .extend(header.solid_wave_vertices.iter().cloned());
        } else {
            self.solid_wave_union
                .extend(header.solid_wave_vertices_merged.iter().cloned());
        }
    }

    pub fn append(
        &mut self,
        header: Header,
        committee: &Committee,
    ) -> DagResult<Option<ProposalParents>> {
        if !self.seen.insert(header.id.clone()) {
            return Ok(None);
        }

        let origin = header.author;
        if header.round == self.expected_round && !self.used.insert(origin) {
            return Ok(None);
        }

        // Accept parents from the whole solid-wave window, but only the newer
        // solid-step sub-window contributes to processing/solid-step checks.
        let current_round = self.expected_round + 1;
        let step_len = committee.solid_step_length();
        let wave_len = committee.solid_wave_length();
        let step_index: Round = ((current_round - 1) % step_len) + 1;
        let wave_index: Round = ((current_round - 1) % wave_len) + 1;
        let regular_weak_start: Round = current_round.saturating_sub(step_index);
        let commit_weak_start: Round = current_round.saturating_sub(wave_index);

        // Add the header to the appropriate list.
        if header.round == self.expected_round {
            self.headers.push(header.id.clone());
            self.extend_step_union(&header);
            self.extend_wave_union(&header);
            self.weight += committee.stake(&origin);
        } else if header.round >= regular_weak_start
            && header.round < self.expected_round
        {
            self.headers.push(header.id.clone());
            self.extend_step_union(&header);
            self.extend_wave_union(&header);
        } else if header.round >= commit_weak_start
            && header.round < regular_weak_start
        {
            self.headers.push(header.id.clone());
            self.extend_wave_union(&header);
        } else {
            return Ok(None);
        }
        debug!(
            "Current round: {}, regular weak range: [{}..={}), commit-only weak range: [{}..={})",
            current_round,
            regular_weak_start,
            self.expected_round,
            commit_weak_start,
            regular_weak_start
        );

        let threshold = committee.processing_threshold(current_round);
        let is_solid_step = committee.is_solid_step(current_round);
        debug!(
            "Advance to round {}: require weight >= {}, solid_step={})",
            current_round, threshold, is_solid_step
        );
        let has_quorum = if is_solid_step {
            self.last_union_set = Some(self.solid_step_union.iter().cloned().collect());
            debug!(
                "Current round: {}, The number of merged solid-step vertices is {}",
                current_round,
                self.solid_step_union.len()
            );
            self.solid_step_union.len() >= threshold as usize
        } else {
            debug!(
                "Current round: {}, The weight is {}, self_has_quorum: {}",
                current_round,
                self.weight,
                self.weight >= threshold
            );
            self.weight >= threshold
        };

        if has_quorum {
            let mut proposal_parents = ProposalParents::from(self.headers.clone());
            proposal_parents.solid_step_union = self.solid_step_union.clone();
            proposal_parents.solid_wave_union = self.solid_wave_union.clone();
            return Ok(Some(proposal_parents));
        }
        Ok(None)
    }
}

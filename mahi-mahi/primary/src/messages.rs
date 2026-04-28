// Copyright(C) Facebook, Inc. and its affiliates.
use crate::error::{DagError, DagResult};
use crate::primary::Round;
use config::{Committee, WorkerId};
use crypto::{Digest, Hash, PublicKey, Signature, SignatureService};
use ed25519_dalek::Digest as _;
use ed25519_dalek::Sha512;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::convert::TryInto;
use std::fmt;

#[derive(Clone, Serialize, Deserialize, Default)]
pub struct Header {
    pub author: PublicKey,
    pub round: Round,
    pub payload: BTreeMap<Digest, WorkerId>,
    pub parents: BTreeSet<Digest>,
    pub id: Digest,
    pub signature: Signature,
    /// Stores the vertices of the first round of the solid step which can be linked to
    pub solid_step_vertices: HashSet<Digest>,
    /// Stores the merged solid-step vertices computed from parents at header creation time.
    /// This preserves the union even when `solid_step_vertices` is re-initialized on init rounds.
    pub solid_step_vertices_merged: HashSet<Digest>,
    /// Stores the vertices of the current solid wave that can be linked to this header.
    pub solid_wave_vertices: HashSet<Digest>,
    /// Stores the merged solid-wave vertices computed from parents at header creation time.
    /// This preserves the union even when `solid_wave_vertices` is re-initialized on wave-end rounds.
    pub solid_wave_vertices_merged: HashSet<Digest>,
}

impl Header {
    pub async fn new(
        author: PublicKey,
        round: Round,
        payload: BTreeMap<Digest, WorkerId>,
        parents: BTreeSet<Digest>,
        signature_service: &mut SignatureService,
    ) -> Self {
        let header = Self {
            author,
            round,
            payload,
            parents,
            id: Digest::default(),
            signature: Signature::default(),
            solid_step_vertices: HashSet::new(),
            solid_step_vertices_merged: HashSet::new(),
            solid_wave_vertices: HashSet::new(),
            solid_wave_vertices_merged: HashSet::new(),
        };
        let id = header.digest();
        let signature = signature_service.request_signature(id.clone()).await;
        Self {
            id,
            signature,
            ..header
        }
    }

    pub fn genesis(committee: &Committee) -> Vec<Self> {
        committee
            .authorities
            .keys()
            .map(|name| {
                let header = Self {
                    author: *name,
                    ..Self::default()
                };
                Self {
                    id: header.digest(),
                    ..header
                }
            })
            .collect()
    }

    pub fn verify(&self, committee: &Committee) -> DagResult<()> {
        // Ensure the header id is well formed.
        ensure!(self.digest() == self.id, DagError::InvalidHeaderId);

        // Ensure the authority has voting rights.
        let voting_rights = committee.stake(&self.author);
        ensure!(voting_rights > 0, DagError::UnknownAuthority(self.author));

        // Ensure all worker ids are correct.
        for worker_id in self.payload.values() {
            committee
                .worker(&self.author, &worker_id)
                .map_err(|_| DagError::MalformedHeader(self.id.clone()))?;
        }

        // Check the signature.
        self.signature
            .verify(&self.id, &self.author)
            .map_err(DagError::from)
    }

    pub fn store_solid_step_vertex(&mut self, vertices: HashSet<Digest>) {
        self.solid_step_vertices.extend(vertices);
    }

    pub fn store_solid_step_merged_vertices(&mut self, vertices: HashSet<Digest>) {
        self.solid_step_vertices_merged.extend(vertices);
    }

    pub fn store_solid_wave_vertex(&mut self, vertices: HashSet<Digest>) {
        self.solid_wave_vertices.extend(vertices);
    }

    pub fn store_solid_wave_merged_vertices(&mut self, vertices: HashSet<Digest>) {
        self.solid_wave_vertices_merged.extend(vertices);
    }

    pub fn round(&self) -> Round {
        self.round
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ProposalParents {
    pub parents: Vec<Digest>,
    pub solid_step_union: HashSet<Digest>,
    pub solid_wave_union: HashSet<Digest>,
}

impl From<Vec<Digest>> for ProposalParents {
    fn from(parents: Vec<Digest>) -> Self {
        Self {
            parents,
            solid_step_union: HashSet::new(),
            solid_wave_union: HashSet::new(),
        }
    }
}

impl Hash for Header {
    fn digest(&self) -> Digest {
        let mut hasher = Sha512::new();
        hasher.update(&self.author);
        hasher.update(self.round.to_le_bytes());
        for (x, y) in &self.payload {
            hasher.update(x);
            hasher.update(y.to_le_bytes());
        }
        for x in &self.parents {
            hasher.update(x);
        }
        Digest(hasher.finalize().as_slice()[..32].try_into().unwrap())
    }
}

impl fmt::Debug for Header {
    fn fmt(&self, f: &mut fmt::Formatter) -> Result<(), fmt::Error> {
        write!(
            f,
            "{}: B{}({}, {})",
            self.id,
            self.round,
            self.author,
            self.payload.keys().map(|x| x.size()).sum::<usize>(),
        )
    }
}

impl fmt::Display for Header {
    fn fmt(&self, f: &mut fmt::Formatter) -> Result<(), fmt::Error> {
        write!(f, "B{}({})", self.round, self.author)
    }
}
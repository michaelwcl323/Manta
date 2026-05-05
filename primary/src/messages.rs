use crate::error::{DagError, DagResult};
use crate::primary::Round;
use config::{Committee, Stake, WorkerId};
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
    /// The tracked round whose authors are reachable through this header.
    pub wave_back_link_target_round: Round,
    /// A bitmap over committee order indicating which tracked-round authors are
    /// reachable through this header.
    pub wave_back_link_author_bitmap: Vec<u8>,
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
            wave_back_link_target_round: 0,
            wave_back_link_author_bitmap: Vec::new(),
        };
        let id = header.digest();
        let signature = signature_service.request_signature(id.clone()).await;
        Self {
            id,
            signature,
            ..header
        }
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

    pub fn store_wave_back_link_summary(&mut self, target_round: Round, bitmap: Vec<u8>) {
        self.wave_back_link_target_round = target_round;
        self.wave_back_link_author_bitmap = bitmap;
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ProposalParents {
    pub parents: Vec<Digest>,
    pub solid_step_union: HashSet<Digest>,
    pub solid_wave_union: HashSet<Digest>,
    pub wave_back_link_target_round: Round,
    pub wave_back_link_author_bitmap: Vec<u8>,
}

impl From<Vec<Digest>> for ProposalParents {
    fn from(parents: Vec<Digest>) -> Self {
        Self {
            parents,
            solid_step_union: HashSet::new(),
            solid_wave_union: HashSet::new(),
            wave_back_link_target_round: 0,
            wave_back_link_author_bitmap: Vec::new(),
        }
    }
}

pub(crate) fn set_author_bit(bitmap: &mut Vec<u8>, index: usize) {
    let byte_index = index / 8;
    let bit_index = index % 8;
    if bitmap.len() <= byte_index {
        bitmap.resize(byte_index + 1, 0);
    }
    bitmap[byte_index] |= 1u8 << bit_index;
}

pub(crate) fn merge_author_bitmaps(bitmap: &mut Vec<u8>, incoming: &[u8]) {
    if bitmap.len() < incoming.len() {
        bitmap.resize(incoming.len(), 0);
    }
    for (dst, src) in bitmap.iter_mut().zip(incoming.iter()) {
        *dst |= *src;
    }
}

pub(crate) fn author_bitmap_stake(committee: &Committee, bitmap: &[u8]) -> Stake {
    committee
        .authorities
        .keys()
        .enumerate()
        .filter(|(index, _)| {
            let byte_index = index / 8;
            let bit_index = index % 8;
            bitmap
                .get(byte_index)
                .is_some_and(|byte| byte & (1u8 << bit_index) != 0)
        })
        .map(|(_, author)| committee.stake(author))
        .sum()
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

#[derive(Clone, Serialize, Deserialize)]
pub struct Vote {
    pub id: Digest,
    pub round: Round,
    pub origin: PublicKey,
    pub author: PublicKey,
    pub signature: Signature,
}

impl Vote {
    pub async fn new(
        header: &Header,
        author: &PublicKey,
        signature_service: &mut SignatureService,
    ) -> Self {
        let vote = Self {
            id: header.id.clone(),
            round: header.round,
            origin: header.author,
            author: *author,
            signature: Signature::default(),
        };
        let signature = signature_service.request_signature(vote.digest()).await;
        Self { signature, ..vote }
    }

    pub fn verify(&self, committee: &Committee) -> DagResult<()> {
        // Ensure the authority has voting rights.
        ensure!(
            committee.stake(&self.author) > 0,
            DagError::UnknownAuthority(self.author)
        );

        // Check the signature.
        self.signature
            .verify(&self.digest(), &self.author)
            .map_err(DagError::from)
    }
}

impl Hash for Vote {
    fn digest(&self) -> Digest {
        let mut hasher = Sha512::new();
        hasher.update(&self.id);
        hasher.update(self.round.to_le_bytes());
        hasher.update(&self.origin);
        Digest(hasher.finalize().as_slice()[..32].try_into().unwrap())
    }
}

impl fmt::Debug for Vote {
    fn fmt(&self, f: &mut fmt::Formatter) -> Result<(), fmt::Error> {
        write!(
            f,
            "{}: V{}({}, {})",
            self.digest(),
            self.round,
            self.author,
            self.id
        )
    }
}

#[derive(Clone, Serialize, Deserialize, Default)]
pub struct Certificate {
    pub header: Header,
    pub votes: Vec<(PublicKey, Signature)>,
}

impl Certificate {
    pub fn genesis(committee: &Committee) -> Vec<Self> {
        committee
            .authorities
            .keys()
            .map(|name| Self {
                header: Header {
                    author: *name,
                    ..Header::default()
                },
                ..Self::default()
            })
            .collect()
    }

    pub fn verify(&self, committee: &Committee) -> DagResult<()> {
        // Genesis certificates are always valid.
        if Self::genesis(committee).contains(self) {
            return Ok(());
        }

        // Check the embedded header.
        self.header.verify(committee)?;

        // Ensure the certificate has a quorum.
        let mut weight = 0;
        let mut used = HashSet::new();
        for (name, _) in self.votes.iter() {
            ensure!(!used.contains(name), DagError::AuthorityReuse(*name));
            let voting_rights = committee.stake(name);
            ensure!(voting_rights > 0, DagError::UnknownAuthority(*name));
            used.insert(*name);
            weight += voting_rights;
        }
        ensure!(
            weight >= committee.quorum_threshold(),
            DagError::CertificateRequiresQuorum
        );

        // Check the signatures.
        Signature::verify_batch(&self.digest(), &self.votes).map_err(DagError::from)
    }

    pub fn round(&self) -> Round {
        self.header.round
    }

    pub fn origin(&self) -> PublicKey {
        self.header.author
    }
}

impl Hash for Certificate {
    fn digest(&self) -> Digest {
        let mut hasher = Sha512::new();
        hasher.update(&self.header.id);
        hasher.update(self.round().to_le_bytes());
        hasher.update(&self.origin());
        Digest(hasher.finalize().as_slice()[..32].try_into().unwrap())
    }
}

impl fmt::Debug for Certificate {
    fn fmt(&self, f: &mut fmt::Formatter) -> Result<(), fmt::Error> {
        write!(
            f,
            "{}: C{}({}, {})",
            self.digest(),
            self.round(),
            self.origin(),
            self.header.id
        )
    }
}

impl PartialEq for Certificate {
    fn eq(&self, other: &Self) -> bool {
        let mut ret = self.header.id == other.header.id;
        ret &= self.round() == other.round();
        ret &= self.origin() == other.origin();
        ret
    }
}

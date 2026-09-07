// Copyright(C) Facebook, Inc. and its affiliates.
use super::*;
use crate::common::{committee, keys};
use std::fs;
use tokio::sync::mpsc::channel;

#[tokio::test]
async fn propose_empty() {
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let (_tx_parents, rx_parents) = channel(1);
    let (_tx_our_digests, rx_our_digests) = channel(1);
    let (tx_headers, mut rx_headers) = channel(1);
    let path = ".db_test_propose_empty";
    let _ = fs::remove_dir_all(path);
    let store = store::Store::new(path).unwrap();

    // Spawn the proposer.
    Proposer::spawn(
        name,
        &committee(),
        signature_service,
        /* header_size */ 1_000,
        /* max_header_delay */ 20,
        /* enable_adaptive_intermediate_spill */ false,
        /* adaptive_intermediate_spill_trigger_digests */ 2,
        /* adaptive_intermediate_spill_cap_digests */ 1,
        /* enable_intermediate_wave_boundary */ false,
        /* rx_core */ rx_parents,
        /* rx_workers */ rx_our_digests,
        /* tx_core */ tx_headers,
        store.clone(),
    );

    // Ensure the proposer makes a correct empty header.
    let header = rx_headers.recv().await.unwrap();
    assert_eq!(header.round, 1);
    assert!(header.payload.is_empty());
    assert!(header.verify(&committee()).is_ok());
}

#[tokio::test]
async fn propose_payload() {
    let committee = committee();
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);
    let genesis_parent = Certificate::genesis(&committee)
        .iter()
        .next()
        .unwrap()
        .digest();
    let (_tx_parents, rx_parents) = channel::<(ProposalParents, Round)>(1);
    let (_tx_our_digests, rx_our_digests) = channel::<(Digest, WorkerId)>(1);
    let (tx_headers, _rx_headers) = channel::<Header>(1);

    let mut unlocked_rounds = HashMap::new();
    unlocked_rounds.insert(
        2,
        UnlockedRound {
            parents: vec![genesis_parent.clone()],
            solid_step_union: HashSet::new(),
            solid_wave_union: HashSet::new(),
            wave_back_link_target_round: 0,
            wave_back_link_author_bitmap: Vec::new(),
            ready_since: Instant::now(),
            unlock_order: 0,
        },
    );
    unlocked_rounds.insert(
        3,
        UnlockedRound {
            parents: vec![genesis_parent],
            solid_step_union: HashSet::new(),
            solid_wave_union: HashSet::new(),
            wave_back_link_target_round: 0,
            wave_back_link_author_bitmap: Vec::new(),
            ready_since: Instant::now(),
            unlock_order: 1,
        },
    );

    let digest = Digest(name.0);
    let proposer = Proposer {
        name,
        node_id: None,
        signature_service,
        header_size: 32,
        max_header_delay: 1_000,
        rx_core: rx_parents,
        rx_workers: rx_our_digests,
        tx_core: tx_headers,
        local_workers: 1,
        enable_adaptive_intermediate_spill: false,
        adaptive_intermediate_spill_trigger_digests: 2,
        adaptive_intermediate_spill_cap_digests: 1,
        enable_intermediate_wave_boundary: false,
        unlocked_rounds,
        proposed_rounds: HashSet::new(),
        next_unlock_order: 2,
        intermediate_digests: VecDeque::new(),
        intermediate_payload_size: 0,
        critical_digests: VecDeque::from(vec![(digest, 0)]),
        critical_payload_size: 32,
        solid_step_length: committee.solid_step_length(),
        solid_wave_length: committee.solid_wave_length(),
        latest_observed_wave: 0,
        parent_grace_delay: Duration::from_millis(0),
    };

    let decision = proposer
        .next_proposal_round(false, true, true, false, false)
        .unwrap();
    assert_eq!(decision.round, 3);
    assert!(decision.include_payload);
}

#[tokio::test]
async fn intermediate_round_still_proposes_empty_with_single_worker() {
    let committee = committee();
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);
    let genesis_parent = Certificate::genesis(&committee)
        .iter()
        .next()
        .unwrap()
        .digest();
    let (tx_parents, rx_parents) = channel::<(ProposalParents, Round)>(1);
    let (tx_our_digests, rx_our_digests) = channel::<(Digest, WorkerId)>(1);
    let (tx_headers, _rx_headers) = channel::<Header>(1);

    drop(tx_parents);
    drop(tx_our_digests);

    let mut unlocked_rounds = HashMap::new();
    unlocked_rounds.insert(
        2,
        UnlockedRound {
            parents: vec![genesis_parent],
            solid_step_union: HashSet::new(),
            solid_wave_union: HashSet::new(),
            wave_back_link_target_round: 0,
            wave_back_link_author_bitmap: Vec::new(),
            ready_since: Instant::now(),
            unlock_order: 0,
        },
    );

    let proposer = Proposer {
        name,
        node_id: None,
        signature_service,
        header_size: 32,
        max_header_delay: 1_000,
        rx_core: rx_parents,
        rx_workers: rx_our_digests,
        tx_core: tx_headers,
        local_workers: 1,
        enable_adaptive_intermediate_spill: false,
        adaptive_intermediate_spill_trigger_digests: 2,
        adaptive_intermediate_spill_cap_digests: 1,
        enable_intermediate_wave_boundary: false,
        unlocked_rounds,
        proposed_rounds: HashSet::new(),
        next_unlock_order: 1,
        intermediate_digests: VecDeque::new(),
        intermediate_payload_size: 0,
        critical_digests: VecDeque::from(vec![(Digest(name.0), 0)]),
        critical_payload_size: 32,
        solid_step_length: committee.solid_step_length(),
        solid_wave_length: committee.solid_wave_length(),
        latest_observed_wave: 0,
        parent_grace_delay: Duration::from_millis(0),
    };

    let decision = proposer
        .next_proposal_round(true, false, false, false, false)
        .unwrap();
    assert_eq!(decision.round, 2);
    assert!(!decision.include_payload);
}

#[tokio::test]
async fn single_worker_never_uses_intermediate_payload_queue() {
    let committee = committee();
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);
    let genesis_parent = Certificate::genesis(&committee)
        .iter()
        .next()
        .unwrap()
        .digest();
    let (tx_parents, rx_parents) = channel::<(ProposalParents, Round)>(1);
    let (tx_our_digests, rx_our_digests) = channel::<(Digest, WorkerId)>(1);
    let (tx_headers, _rx_headers) = channel::<Header>(1);

    drop(tx_parents);
    drop(tx_our_digests);

    let mut unlocked_rounds = HashMap::new();
    unlocked_rounds.insert(
        2,
        UnlockedRound {
            parents: vec![genesis_parent],
            solid_step_union: HashSet::new(),
            solid_wave_union: HashSet::new(),
            wave_back_link_target_round: 0,
            wave_back_link_author_bitmap: Vec::new(),
            ready_since: Instant::now(),
            unlock_order: 0,
        },
    );

    let proposer = Proposer {
        name,
        node_id: None,
        signature_service,
        header_size: 32,
        max_header_delay: 1_000,
        rx_core: rx_parents,
        rx_workers: rx_our_digests,
        tx_core: tx_headers,
        local_workers: 1,
        enable_adaptive_intermediate_spill: false,
        adaptive_intermediate_spill_trigger_digests: 2,
        adaptive_intermediate_spill_cap_digests: 1,
        enable_intermediate_wave_boundary: false,
        unlocked_rounds,
        proposed_rounds: HashSet::new(),
        next_unlock_order: 1,
        intermediate_digests: VecDeque::from(vec![(Digest([7; 32]), 0)]),
        intermediate_payload_size: 32,
        critical_digests: VecDeque::new(),
        critical_payload_size: 0,
        solid_step_length: committee.solid_step_length(),
        solid_wave_length: committee.solid_wave_length(),
        latest_observed_wave: 0,
        parent_grace_delay: Duration::from_millis(0),
    };

    let decision = proposer
        .next_proposal_round(false, false, false, true, true);
    assert!(decision.is_none());

    let decision = proposer
        .next_proposal_round(true, false, false, false, false)
        .unwrap();
    assert_eq!(decision.round, 2);
    assert!(!decision.include_payload);
}

#[tokio::test]
async fn adaptive_spill_prefers_critical_until_small_intermediate_window_fills() {
    let committee = committee();
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);
    let (_tx_parents, rx_parents) = channel::<(ProposalParents, Round)>(1);
    let (_tx_our_digests, rx_our_digests) = channel::<(Digest, WorkerId)>(1);
    let (tx_headers, _rx_headers) = channel::<Header>(1);

    let mut proposer = Proposer {
        name,
        node_id: None,
        signature_service,
        header_size: 32,
        max_header_delay: 1_000,
        rx_core: rx_parents,
        rx_workers: rx_our_digests,
        tx_core: tx_headers,
        local_workers: 1,
        enable_adaptive_intermediate_spill: true,
        adaptive_intermediate_spill_trigger_digests: 2,
        adaptive_intermediate_spill_cap_digests: 1,
        enable_intermediate_wave_boundary: false,
        unlocked_rounds: HashMap::new(),
        proposed_rounds: HashSet::new(),
        next_unlock_order: 0,
        intermediate_digests: VecDeque::new(),
        intermediate_payload_size: 0,
        critical_digests: VecDeque::new(),
        critical_payload_size: 0,
        solid_step_length: committee.solid_step_length(),
        solid_wave_length: committee.solid_wave_length(),
        latest_observed_wave: 0,
        parent_grace_delay: Duration::from_millis(0),
    };

    assert_eq!(proposer.payload_queue_for_worker(0), RoundClass::Critical);
    proposer.critical_digests = VecDeque::from(vec![(Digest([1; 32]), 0), (Digest([2; 32]), 0)]);
    assert_eq!(proposer.payload_queue_for_worker(0), RoundClass::Intermediate);
    proposer.intermediate_digests = VecDeque::from(vec![(Digest([3; 32]), 0)]);
    assert_eq!(proposer.payload_queue_for_worker(0), RoundClass::Critical);
}

#[tokio::test]
async fn intermediate_round_uses_payload_from_dedicated_queue() {
    let committee = committee();
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);
    let genesis_parent = Certificate::genesis(&committee)
        .iter()
        .next()
        .unwrap()
        .digest();
    let (tx_parents, rx_parents) = channel::<(ProposalParents, Round)>(1);
    let (tx_our_digests, rx_our_digests) = channel::<(Digest, WorkerId)>(1);
    let (tx_headers, _rx_headers) = channel::<Header>(1);

    drop(tx_parents);
    drop(tx_our_digests);

    let mut unlocked_rounds = HashMap::new();
    unlocked_rounds.insert(
        2,
        UnlockedRound {
            parents: vec![genesis_parent],
            solid_step_union: HashSet::new(),
            solid_wave_union: HashSet::new(),
            wave_back_link_target_round: 0,
            wave_back_link_author_bitmap: Vec::new(),
            ready_since: Instant::now(),
            unlock_order: 0,
        },
    );

    let proposer = Proposer {
        name,
        node_id: None,
        signature_service,
        header_size: 32,
        max_header_delay: 1_000,
        rx_core: rx_parents,
        rx_workers: rx_our_digests,
        tx_core: tx_headers,
        local_workers: 2,
        enable_adaptive_intermediate_spill: false,
        adaptive_intermediate_spill_trigger_digests: 2,
        adaptive_intermediate_spill_cap_digests: 1,
        enable_intermediate_wave_boundary: false,
        unlocked_rounds,
        proposed_rounds: HashSet::new(),
        next_unlock_order: 1,
        intermediate_digests: VecDeque::from(vec![(Digest([1; 32]), 0)]),
        intermediate_payload_size: 32,
        critical_digests: VecDeque::from(vec![(Digest([2; 32]), 1)]),
        critical_payload_size: 32,
        solid_step_length: committee.solid_step_length(),
        solid_wave_length: committee.solid_wave_length(),
        latest_observed_wave: 0,
        parent_grace_delay: Duration::from_millis(0),
    };

    let decision = proposer
        .next_proposal_round(false, false, false, true, true)
        .unwrap();
    assert_eq!(decision.round, 2);
    assert!(decision.include_payload);
}

#[tokio::test]
async fn critical_unlock_keeps_existing_intermediate_round() {
    let committee = committee();
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);
    let genesis_parent = Certificate::genesis(&committee)
        .iter()
        .next()
        .unwrap()
        .digest();
    let (_tx_parents, rx_parents) = channel::<(ProposalParents, Round)>(1);
    let (_tx_our_digests, rx_our_digests) = channel::<(Digest, WorkerId)>(1);
    let (tx_headers, _rx_headers) = channel::<Header>(1);

    let mut unlocked_rounds = HashMap::new();
    unlocked_rounds.insert(
        2,
        UnlockedRound {
            parents: vec![genesis_parent.clone()],
            solid_step_union: HashSet::new(),
            solid_wave_union: HashSet::new(),
            wave_back_link_target_round: 0,
            wave_back_link_author_bitmap: Vec::new(),
            ready_since: Instant::now(),
            unlock_order: 0,
        },
    );

    let mut proposer = Proposer {
        name,
        node_id: None,
        signature_service,
        header_size: 32,
        max_header_delay: 1_000,
        rx_core: rx_parents,
        rx_workers: rx_our_digests,
        tx_core: tx_headers,
        local_workers: 2,
        enable_adaptive_intermediate_spill: false,
        adaptive_intermediate_spill_trigger_digests: 2,
        adaptive_intermediate_spill_cap_digests: 1,
        enable_intermediate_wave_boundary: false,
        unlocked_rounds,
        proposed_rounds: HashSet::new(),
        next_unlock_order: 1,
        intermediate_digests: VecDeque::new(),
        intermediate_payload_size: 0,
        critical_digests: VecDeque::new(),
        critical_payload_size: 0,
        solid_step_length: committee.solid_step_length(),
        solid_wave_length: committee.solid_wave_length(),
        latest_observed_wave: 0,
        parent_grace_delay: Duration::from_millis(0),
    };

    proposer.unlock_round(3, ProposalParents::from(vec![genesis_parent]));

    assert!(proposer.unlocked_rounds.contains_key(&2));
    assert!(proposer.unlocked_rounds.contains_key(&3));
}

#[tokio::test]
async fn intermediate_round_is_kept_after_critical_started() {
    let committee = committee();
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);
    let genesis_parent = Certificate::genesis(&committee)
        .iter()
        .next()
        .unwrap()
        .digest();
    let (_tx_parents, rx_parents) = channel::<(ProposalParents, Round)>(1);
    let (_tx_our_digests, rx_our_digests) = channel::<(Digest, WorkerId)>(1);
    let (tx_headers, _rx_headers) = channel::<Header>(1);

    let mut proposer = Proposer {
        name,
        node_id: None,
        signature_service,
        header_size: 32,
        max_header_delay: 1_000,
        rx_core: rx_parents,
        rx_workers: rx_our_digests,
        tx_core: tx_headers,
        local_workers: 2,
        enable_adaptive_intermediate_spill: false,
        adaptive_intermediate_spill_trigger_digests: 2,
        adaptive_intermediate_spill_cap_digests: 1,
        enable_intermediate_wave_boundary: false,
        unlocked_rounds: HashMap::new(),
        proposed_rounds: [3u64].iter().copied().collect(),
        next_unlock_order: 0,
        intermediate_digests: VecDeque::new(),
        intermediate_payload_size: 0,
        critical_digests: VecDeque::new(),
        critical_payload_size: 0,
        solid_step_length: committee.solid_step_length(),
        solid_wave_length: committee.solid_wave_length(),
        latest_observed_wave: 0,
        parent_grace_delay: Duration::from_millis(0),
    };

    proposer.unlock_round(2, ProposalParents::from(vec![genesis_parent]));

    assert!(proposer.unlocked_rounds.contains_key(&2));
}

#[tokio::test]
async fn wave_end_drops_intermediate_rounds_from_previous_wave() {
    let committee = committee();
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);
    let genesis_parent = Certificate::genesis(&committee)
        .iter()
        .next()
        .unwrap()
        .digest();
    let (_tx_parents, rx_parents) = channel::<(ProposalParents, Round)>(1);
    let (_tx_our_digests, rx_our_digests) = channel::<(Digest, WorkerId)>(1);
    let (tx_headers, _rx_headers) = channel::<Header>(1);

    let mut unlocked_rounds = HashMap::new();
    unlocked_rounds.insert(
        2,
        UnlockedRound {
            parents: vec![genesis_parent.clone()],
            solid_step_union: HashSet::new(),
            solid_wave_union: HashSet::new(),
            wave_back_link_target_round: 0,
            wave_back_link_author_bitmap: Vec::new(),
            ready_since: Instant::now(),
            unlock_order: 0,
        },
    );
    unlocked_rounds.insert(
        4,
        UnlockedRound {
            parents: vec![genesis_parent.clone()],
            solid_step_union: HashSet::new(),
            solid_wave_union: HashSet::new(),
            wave_back_link_target_round: 0,
            wave_back_link_author_bitmap: Vec::new(),
            ready_since: Instant::now(),
            unlock_order: 1,
        },
    );
    unlocked_rounds.insert(
        3,
        UnlockedRound {
            parents: vec![genesis_parent.clone()],
            solid_step_union: HashSet::new(),
            solid_wave_union: HashSet::new(),
            wave_back_link_target_round: 0,
            wave_back_link_author_bitmap: Vec::new(),
            ready_since: Instant::now(),
            unlock_order: 2,
        },
    );

    let mut proposer = Proposer {
        name,
        node_id: None,
        signature_service,
        header_size: 32,
        max_header_delay: 1_000,
        rx_core: rx_parents,
        rx_workers: rx_our_digests,
        tx_core: tx_headers,
        local_workers: 2,
        enable_adaptive_intermediate_spill: false,
        adaptive_intermediate_spill_trigger_digests: 2,
        adaptive_intermediate_spill_cap_digests: 1,
        enable_intermediate_wave_boundary: true,
        unlocked_rounds,
        proposed_rounds: HashSet::new(),
        next_unlock_order: 3,
        intermediate_digests: VecDeque::new(),
        intermediate_payload_size: 0,
        critical_digests: VecDeque::new(),
        critical_payload_size: 0,
        solid_step_length: committee.solid_step_length(),
        solid_wave_length: committee.solid_wave_length(),
        latest_observed_wave: 0,
        parent_grace_delay: Duration::from_millis(0),
    };

    // Round 5 starts the next solid wave (σ=κ=2 ⇒ wave length 4).
    proposer.unlock_round(5, ProposalParents::from(vec![genesis_parent.clone()]));

    assert!(!proposer.unlocked_rounds.contains_key(&2));
    assert!(!proposer.unlocked_rounds.contains_key(&4));
    assert!(proposer.unlocked_rounds.contains_key(&3));
    assert!(proposer.unlocked_rounds.contains_key(&5));
    assert_eq!(proposer.latest_observed_wave, 1);

    // Late unlock of a previous-wave intermediate must also be rejected.
    proposer.unlock_round(2, ProposalParents::from(vec![genesis_parent]));
    assert!(!proposer.unlocked_rounds.contains_key(&2));
}

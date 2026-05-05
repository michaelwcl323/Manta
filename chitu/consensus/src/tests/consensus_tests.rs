use super::*;
use config::{Authority, PrimaryAddresses};
use crypto::{generate_keypair, Digest, SecretKey};
use primary::{Certificate, Header};
use rand::rngs::StdRng;
use rand::SeedableRng as _;
use std::collections::{BTreeSet, HashMap};
use tokio::sync::mpsc::{channel, Receiver};

// Fixture
fn keys() -> Vec<(PublicKey, SecretKey)> {
    let mut rng = StdRng::from_seed([0; 32]);
    (0..4).map(|_| generate_keypair(&mut rng)).collect()
}

// Fixture
pub fn mock_committee() -> Committee {
    Committee {
        authorities: keys()
            .iter()
            .map(|(id, _)| {
                (
                    *id,
                    Authority {
                        stake: 1,
                        primary: PrimaryAddresses {
                            primary_to_primary: "0.0.0.0:0".parse().unwrap(),
                            worker_to_primary: "0.0.0.0:0".parse().unwrap(),
                        },
                        workers: HashMap::default(),
                    },
                )
            })
            .collect(),
        sigma: 1,
        kappa: 3,
        reference: 3,
        coverage: 3,
    }
}

// Fixture
fn mock_certificate(origin: PublicKey, round: Round, parents: impl IntoIterator<Item = Digest>) -> Certificate {
    let mut parent_set = BTreeSet::new();
    parent_set.extend(parents);
    Certificate {
        header: Header {
            author: origin,
            round,
            parents: parent_set,
            ..Header::default()
        },
        ..Certificate::default()
    }
}

fn make_consensus(committee: &Committee) -> (Consensus, Receiver<Certificate>, Receiver<Certificate>) {
    let (_tx_waiter, rx_waiter) = channel(1);
    let (tx_primary, rx_primary) = channel(16);
    let (tx_output, rx_output) = channel(16);
    let authorities: Vec<_> = committee.authorities.keys().copied().collect();
    let author_to_node = authorities
        .iter()
        .copied()
        .enumerate()
        .map(|(index, authority)| (authority, index))
        .collect();

    (
        Consensus {
            committee: committee.clone(),
            authorities,
            author_to_node,
            gc_depth: 50,
            rx_primary: rx_waiter,
            tx_primary,
            tx_output,
            genesis: Certificate::genesis(committee),
        },
        rx_primary,
        rx_output,
    )
}

fn leader_author(committee: &Committee) -> PublicKey {
    *committee.authorities.keys().next().unwrap()
}

fn non_leader_authors(committee: &Committee) -> Vec<PublicKey> {
    let leader = leader_author(committee);
    committee
        .authorities
        .keys()
        .copied()
        .filter(|author| *author != leader)
        .collect()
}

#[tokio::test]
async fn slow_path_commits_one_valent_buffered_round() {
    let committee = mock_committee();
    let leader = leader_author(&committee);
    let others = non_leader_authors(&committee);
    let target = mock_certificate(others[0], 1, []);
    let bridge_a = mock_certificate(others[1], 2, [target.digest()]);
    let bridge_b = mock_certificate(others[2], 2, [target.digest()]);
    let leader_cert = mock_certificate(leader, 3, [bridge_a.digest(), bridge_b.digest()]);
    let support_a = mock_certificate(others[0], 4, [leader_cert.digest()]);
    let support_b = mock_certificate(others[1], 4, [leader_cert.digest()]);

    let (consensus, _rx_primary, mut rx_output) = make_consensus(&committee);
    let mut state = State::new(Certificate::genesis(&committee));
    for certificate in [
        target.clone(),
        bridge_a.clone(),
        bridge_b.clone(),
        leader_cert.clone(),
        support_a,
        support_b,
    ] {
        state.insert(certificate);
    }
    state.set_commit_status(&target, CommitStatus::Bivalent);
    state.note_buffered_round(1);

    assert!(consensus.slow_path(4, &mut state).await);
    assert_eq!(
        state.find_certificate(&target.digest()).unwrap().2,
        CommitStatus::OneValent
    );
    assert!(state.committed_digests.contains(&target.digest()));

    let committed = rx_output.recv().await.unwrap();
    assert_eq!(committed.digest(), target.digest());
}

#[tokio::test]
async fn slow_path_sets_zero_when_valid_leader_lacks_strong_observe() {
    let committee = mock_committee();
    let leader = leader_author(&committee);
    let others = non_leader_authors(&committee);
    let target = mock_certificate(others[0], 1, []);
    let bridge_a = mock_certificate(others[1], 2, [target.digest()]);
    let bridge_b = mock_certificate(others[2], 2, []);
    let leader_cert = mock_certificate(leader, 3, [bridge_a.digest(), bridge_b.digest()]);
    let support_a = mock_certificate(others[0], 4, [leader_cert.digest()]);
    let support_b = mock_certificate(others[1], 4, [leader_cert.digest()]);

    let (consensus, _rx_primary, mut rx_output) = make_consensus(&committee);
    let mut state = State::new(Certificate::genesis(&committee));
    for certificate in [target.clone(), bridge_a, bridge_b, leader_cert, support_a, support_b] {
        state.insert(certificate);
    }
    state.set_commit_status(&target, CommitStatus::Bivalent);
    state.note_buffered_round(1);

    assert!(consensus.slow_path(4, &mut state).await);
    assert_eq!(
        state.find_certificate(&target.digest()).unwrap().2,
        CommitStatus::ZeroValent
    );
    assert!(!state.committed_digests.contains(&target.digest()));
    assert!(rx_output.try_recv().is_err());
}

#[tokio::test]
async fn later_valid_leader_recursively_validates_previous_leader() {
    let committee = mock_committee();
    let leader = leader_author(&committee);
    let others = non_leader_authors(&committee);
    let target = mock_certificate(others[0], 1, []);
    let bridge_a = mock_certificate(others[1], 2, [target.digest()]);
    let bridge_b = mock_certificate(others[2], 2, [target.digest()]);
    let leader_round_3 = mock_certificate(leader, 3, [bridge_a.digest(), bridge_b.digest()]);
    let leader_round_4 = mock_certificate(leader, 4, [leader_round_3.digest()]);
    let support_a = mock_certificate(others[0], 5, [leader_round_4.digest()]);
    let support_b = mock_certificate(others[1], 5, [leader_round_4.digest()]);

    let (consensus, _rx_primary, mut rx_output) = make_consensus(&committee);
    let mut state = State::new(Certificate::genesis(&committee));
    for certificate in [
        target.clone(),
        bridge_a.clone(),
        bridge_b.clone(),
        leader_round_3.clone(),
        leader_round_4,
        support_a,
        support_b,
    ] {
        state.insert(certificate);
    }
    state.set_commit_status(&target, CommitStatus::Bivalent);
    state.note_buffered_round(1);

    let mut validity_cache = HashMap::new();
    assert!(consensus.is_valid_leader_round(3, &state, &mut validity_cache));
    assert!(consensus.slow_path(5, &mut state).await);
    assert_eq!(
        state.find_certificate(&target.digest()).unwrap().2,
        CommitStatus::OneValent
    );
    assert_eq!(rx_output.recv().await.unwrap().digest(), target.digest());
}

#[tokio::test]
async fn fast_path_rechecks_when_new_vertices_arrive_after_bivalent() {
    let committee = mock_committee();
    let leader = leader_author(&committee);
    let others = non_leader_authors(&committee);
    let target = mock_certificate(others[0], 1, []);

    let round_two_a = Certificate {
        header: Header {
            author: others[1],
            round: 2,
            solid_step_vertices: vec![target.digest()].into_iter().collect(),
            ..Header::default()
        },
        ..Certificate::default()
    };
    let round_two_b = Certificate {
        header: Header {
            author: others[2],
            round: 2,
            solid_step_vertices: HashSet::new(),
            ..Header::default()
        },
        ..Certificate::default()
    };
    let round_two_c = Certificate {
        header: Header {
            author: leader,
            round: 2,
            solid_step_vertices: vec![target.digest()].into_iter().collect(),
            ..Header::default()
        },
        ..Certificate::default()
    };
    let round_two_d = Certificate {
        header: Header {
            author: others[0],
            round: 2,
            solid_step_vertices: vec![target.digest()].into_iter().collect(),
            ..Header::default()
        },
        ..Certificate::default()
    };

    let (consensus, _rx_primary, mut rx_output) = make_consensus(&committee);
    let mut state = State::new(Certificate::genesis(&committee));
    state.insert(target.clone());
    state.insert(round_two_a);
    state.insert(round_two_b);
    state.insert(round_two_c);

    assert!(!consensus.fast_path(2, &mut state).await);
    assert_eq!(
        state.find_certificate(&target.digest()).unwrap().2,
        CommitStatus::Bivalent
    );

    state.insert(round_two_d);
    assert!(consensus.fast_path(2, &mut state).await);
    assert_eq!(
        state.find_certificate(&target.digest()).unwrap().2,
        CommitStatus::OneValent
    );
    assert_eq!(rx_output.recv().await.unwrap().digest(), target.digest());
}

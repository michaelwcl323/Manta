use super::*;
use crate::common::{committee, keys};
use std::fs;
use store::Store;
use tokio::sync::mpsc::channel;

#[tokio::test]
async fn propose_empty() {
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let (_tx_parents, rx_parents) = channel(1);
    let (_tx_our_digests, rx_our_digests) = channel(1);
    let (tx_headers, mut rx_headers) = channel(1);

    // Create a new test store.
    let path = ".db_test_propose_empty";
    let _ = fs::remove_dir_all(path);
    let store = Store::new(path).unwrap();

    // Spawn the proposer.
    Proposer::spawn(
        name,
        &committee(),
        signature_service,
        /* header_size */ 1_000,
        /* max_header_delay */ 20,
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
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let (tx_parents, rx_parents) = channel(1);
    let (tx_our_digests, rx_our_digests) = channel(1);
    let (tx_headers, mut rx_headers) = channel(1);

    // Create a new test store.
    let path = ".db_test_propose_payload";
    let _ = fs::remove_dir_all(path);
    let store = Store::new(path).unwrap();

    // Spawn the proposer.
    Proposer::spawn(
        name,
        &committee(),
        signature_service,
        /* header_size */ 32,
        /* max_header_delay */ 1_000_000, // Ensure it is not triggered.
        /* rx_core */ rx_parents,
        /* rx_workers */ rx_our_digests,
        /* tx_core */ tx_headers,
        store.clone(),
    );

    // Bootstrap round 1 is proposed immediately and remains empty.
    let bootstrap_header = rx_headers.recv().await.unwrap();
    assert_eq!(bootstrap_header.round, 1);
    assert!(bootstrap_header.payload.is_empty());

    // Send enough digests for the next header payload.
    let digest = Digest(name.0);
    let worker_id = 0;
    tx_our_digests
        .send((digest.clone(), worker_id))
        .await
        .unwrap();

    tx_parents
        .send((
            ProposalParents::from(
                Certificate::genesis(&committee())
                    .iter()
                    .map(|x| x.digest())
                    .collect::<Vec<_>>(),
            ),
            1,
        ))
        .await
        .unwrap();

    // Ensure the proposer makes a correct payload-carrying header for the unlocked next round.
    let header = rx_headers.recv().await.unwrap();
    assert_eq!(header.round, 2);
    assert_eq!(header.payload.get(&digest), Some(&worker_id));
    assert!(header.verify(&committee()).is_ok());
}

// Copyright(C) Facebook, Inc. and its affiliates.
use super::*;
use crate::common::{committee, keys};
use ed25519_dalek::Digest as _;
use ed25519_dalek::Sha512;
use std::convert::TryInto;
use tokio::sync::mpsc::channel;
use tokio::time::{timeout, Duration};

fn digest_from_bytes(bytes: &[u8]) -> Digest {
    Digest(Sha512::digest(bytes).as_slice()[..32].try_into().unwrap())
}

#[tokio::test]
async fn propose_empty() {
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let (_tx_parents, rx_parents) = channel(1);
    let (_tx_our_digests, rx_our_digests) = channel(1);
    let (tx_headers, mut rx_headers) = channel(1);

    // Spawn the proposer.
    Proposer::spawn(
        name,
        &committee(),
        signature_service,
        /* header_size */ 1_000,
        /* max_header_batches */ None,
        /* max_header_delay */ 20,
        /* rx_core */ rx_parents,
        /* rx_workers */ rx_our_digests,
        /* tx_core */ tx_headers,
    );

    // Ensure the proposer makes a correct empty header.
    let header = rx_headers.recv().await.unwrap();
    assert_eq!(header.round, 1);
    assert!(header.payload.is_empty());
    assert!(header.inline_payload.is_none());
    assert!(header.verify(&committee()).is_ok());
}

#[tokio::test]
async fn propose_payload() {
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let (_tx_parents, rx_parents) = channel(1);
    let (tx_our_digests, rx_our_digests) = channel(1);
    let (tx_headers, mut rx_headers) = channel(1);

    // Spawn the proposer.
    Proposer::spawn(
        name,
        &committee(),
        signature_service,
        /* header_size */ 4,
        /* max_header_batches */ None,
        /* max_header_delay */ 1_000_000, // Ensure it is not triggered.
        /* rx_core */ rx_parents,
        /* rx_workers */ rx_our_digests,
        /* tx_core */ tx_headers,
    );

    let worker_id = 0;
    let batch = vec![1, 2, 3, 4];
    let digest = digest_from_bytes(&batch);
    tx_our_digests
        .send((digest.clone(), worker_id, batch.clone()))
        .await
        .unwrap();

    // Ensure the proposer makes a correct header from the provided payload.
    let header = rx_headers.recv().await.unwrap();
    assert_eq!(header.round, 1);
    assert_eq!(header.payload.get(&digest), Some(&worker_id));
    assert_eq!(
        header.inline_payload.as_ref().and_then(|x| x.get(&digest)),
        Some(&batch)
    );
    assert!(header.verify(&committee()).is_ok());
}

#[tokio::test]
async fn inspect_vertex_contents_with_inline_payload() {
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let (tx_parents, rx_parents) = channel(1);
    let (tx_our_digests, rx_our_digests) = channel(2);
    let (tx_headers, mut rx_headers) = channel(2);

    let batch_a = b"zipf-hot-key".to_vec();
    let batch_b = b"geo-uniform-cold-key-range".to_vec();
    let bootstrap_batch = b"bootstrap-round-one".to_vec();

    Proposer::spawn(
        name,
        &committee(),
        signature_service,
        /* header_size */ batch_a.len() + batch_b.len(),
        /* max_header_batches */ None,
        /* max_header_delay */ 1_000_000,
        /* rx_core */ rx_parents,
        /* rx_workers */ rx_our_digests,
        /* tx_core */ tx_headers,
    );

    let worker_id = 0;
    let bootstrap_digest = digest_from_bytes(&bootstrap_batch);
    tx_our_digests
        .send((bootstrap_digest.clone(), worker_id, bootstrap_batch.clone()))
        .await
        .unwrap();

    let bootstrap_header = rx_headers.recv().await.unwrap();
    assert_eq!(bootstrap_header.round, 1);

    let digest_a = digest_from_bytes(&batch_a);
    let digest_b = digest_from_bytes(&batch_b);

    tx_our_digests
        .send((digest_a.clone(), worker_id, batch_a.clone()))
        .await
        .unwrap();
    tx_our_digests
        .send((digest_b.clone(), worker_id, batch_b.clone()))
        .await
        .unwrap();

    tokio::time::sleep(Duration::from_millis(10)).await;
    assert!(rx_headers.try_recv().is_err());

    tx_parents
        .send((vec![digest_from_bytes(b"parent-a"), digest_from_bytes(b"parent-b")], 1))
        .await
        .unwrap();

    let header = rx_headers.recv().await.unwrap();
    assert_eq!(header.round, 2);
    let inline_payload = header.inline_payload.as_ref().unwrap();
    let serialized_header = bincode::serialize(&header).unwrap();

    println!("=== Vertex Inspection ===");
    println!("round: {}", header.round);
    println!("payload entries: {}", header.payload.len());
    println!("inline payload entries: {}", inline_payload.len());
    println!("serialized header bytes: {}", serialized_header.len());
    println!("payload map: {:?}", header.payload);
    println!(
        "inline payload sizes: {:?}",
        inline_payload
            .iter()
            .map(|(digest, bytes)| (format!("{:?}", digest), bytes.len()))
            .collect::<Vec<_>>()
    );
    println!(
        "inline payload bytes: {:?}",
        inline_payload
            .iter()
            .map(|(digest, bytes)| (format!("{:?}", digest), bytes.clone()))
            .collect::<Vec<_>>()
    );

    assert_eq!(header.payload.get(&digest_a), Some(&worker_id));
    assert_eq!(header.payload.get(&digest_b), Some(&worker_id));
    assert_eq!(inline_payload.get(&digest_a), Some(&batch_a));
    assert_eq!(inline_payload.get(&digest_b), Some(&batch_b));
    assert!(header.verify(&committee()).is_ok());
}

#[tokio::test]
async fn compare_small_and_large_workload_vertex_sizes() {
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let (_tx_parents, rx_parents) = channel(1);
    let (tx_our_digests, rx_our_digests) = channel(2);
    let (tx_headers, mut rx_headers) = channel(2);

    let small_batch = b"hot".to_vec();
    let large_batch = b"this-is-a-much-larger-vertex-workload-example".to_vec();

    Proposer::spawn(
        name,
        &committee(),
        signature_service,
        /* header_size */ small_batch.len(),
        /* max_header_batches */ None,
        /* max_header_delay */ 1_000_000,
        /* rx_core */ rx_parents,
        /* rx_workers */ rx_our_digests,
        /* tx_core */ tx_headers,
    );

    let worker_id = 0;
    let small_digest = digest_from_bytes(&small_batch);
    tx_our_digests
        .send((small_digest.clone(), worker_id, small_batch.clone()))
        .await
        .unwrap();

    let small_header = rx_headers.recv().await.unwrap();
    let small_serialized = bincode::serialize(&small_header).unwrap();
    let small_inline_bytes: usize = small_header
        .inline_payload
        .as_ref()
        .unwrap()
        .values()
        .map(|x| x.len())
        .sum();

    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);
    let (_tx_parents, rx_parents) = channel(1);
    let (tx_our_digests, rx_our_digests) = channel(2);
    let (tx_headers, mut rx_headers) = channel(2);

    Proposer::spawn(
        name,
        &committee(),
        signature_service,
        /* header_size */ large_batch.len(),
        /* max_header_batches */ None,
        /* max_header_delay */ 1_000_000,
        /* rx_core */ rx_parents,
        /* rx_workers */ rx_our_digests,
        /* tx_core */ tx_headers,
    );

    let large_digest = digest_from_bytes(&large_batch);
    tx_our_digests
        .send((large_digest.clone(), worker_id, large_batch.clone()))
        .await
        .unwrap();

    let large_header = rx_headers.recv().await.unwrap();
    let large_serialized = bincode::serialize(&large_header).unwrap();
    let large_inline_bytes: usize = large_header
        .inline_payload
        .as_ref()
        .unwrap()
        .values()
        .map(|x| x.len())
        .sum();

    println!("=== Vertex Size Comparison ===");
    println!(
        "small workload: payload_bytes={}, serialized_header_bytes={}",
        small_inline_bytes,
        small_serialized.len()
    );
    println!(
        "large workload: payload_bytes={}, serialized_header_bytes={}",
        large_inline_bytes,
        large_serialized.len()
    );
    println!(
        "delta: payload_bytes={}, serialized_header_bytes={}",
        large_inline_bytes as isize - small_inline_bytes as isize,
        large_serialized.len() as isize - small_serialized.len() as isize
    );

    assert!(large_inline_bytes > small_inline_bytes);
    assert!(large_serialized.len() > small_serialized.len());
}

#[tokio::test]
async fn propose_payload_when_batch_count_threshold_is_reached() {
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let (tx_parents, rx_parents) = channel(1);
    let (tx_our_digests, rx_our_digests) = channel(2);
    let (tx_headers, mut rx_headers) = channel(2);

    Proposer::spawn(
        name,
        &committee(),
        signature_service,
        /* header_size */ 1_000_000,
        /* max_header_batches */ Some(2),
        /* max_header_delay */ 1_000_000,
        /* rx_core */ rx_parents,
        /* rx_workers */ rx_our_digests,
        /* tx_core */ tx_headers,
    );

    let worker_id = 0;
    let bootstrap_batch = vec![0, 1, 2, 3];
    let bootstrap_digest = digest_from_bytes(&bootstrap_batch);
    tx_our_digests
        .send((bootstrap_digest.clone(), worker_id, bootstrap_batch.clone()))
        .await
        .unwrap();
    let bootstrap_header = rx_headers.recv().await.unwrap();
    assert_eq!(bootstrap_header.round, 1);

    let batch_a = vec![1, 2, 3, 4];
    let digest_a = digest_from_bytes(&batch_a);

    tx_our_digests
        .send((digest_a.clone(), worker_id, batch_a.clone()))
        .await
        .unwrap();
    assert!(rx_headers.try_recv().is_err());

    tx_parents
        .send((vec![digest_from_bytes(b"parent-c")], 1))
        .await
        .unwrap();

    let header = rx_headers.recv().await.unwrap();
    let inline_payload = header.inline_payload.as_ref().unwrap();
    assert_eq!(header.round, 2);
    assert_eq!(header.payload.len(), 1);
    assert_eq!(header.payload.get(&digest_a), Some(&worker_id));
    assert_eq!(inline_payload.get(&digest_a), Some(&batch_a));
    assert!(header.verify(&committee()).is_ok());
}

#[tokio::test]
async fn payload_arriving_during_empty_header_timeout_becomes_non_empty_header() {
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let (tx_parents, rx_parents) = channel(1);
    let (tx_our_digests, rx_our_digests) = channel(2);
    let (tx_headers, mut rx_headers) = channel(2);

    Proposer::spawn(
        name,
        &committee(),
        signature_service,
        /* header_size */ 1_000_000,
        /* max_header_batches */ Some(2),
        /* max_header_delay */ 50,
        /* rx_core */ rx_parents,
        /* rx_workers */ rx_our_digests,
        /* tx_core */ tx_headers,
    );

    let worker_id = 0;
    let bootstrap_batch = vec![9, 9, 9, 9];
    let bootstrap_digest = digest_from_bytes(&bootstrap_batch);
    tx_our_digests
        .send((bootstrap_digest.clone(), worker_id, bootstrap_batch.clone()))
        .await
        .unwrap();
    let bootstrap_header = rx_headers.recv().await.unwrap();
    assert_eq!(bootstrap_header.round, 1);

    tx_parents
        .send((vec![digest_from_bytes(b"parent-d")], 1))
        .await
        .unwrap();

    let batch = vec![7, 7, 7, 7];
    let digest = digest_from_bytes(&batch);
    tx_our_digests
        .send((digest.clone(), worker_id, batch.clone()))
        .await
        .unwrap();

    let header = timeout(Duration::from_millis(200), rx_headers.recv())
        .await
        .unwrap()
        .unwrap();

    assert_eq!(header.round, 2);
    assert_eq!(header.payload.len(), 1);
    assert_eq!(header.payload.get(&digest), Some(&worker_id));
    assert_eq!(
        header.inline_payload.as_ref().and_then(|x| x.get(&digest)),
        Some(&batch)
    );
}

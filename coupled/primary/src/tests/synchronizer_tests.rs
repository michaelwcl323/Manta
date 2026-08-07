// Copyright(C) Facebook, Inc. and its affiliates.
use super::*;
use crate::common::{committee, header, keys};
use ed25519_dalek::Digest as _;
use ed25519_dalek::Sha512;
use std::convert::TryInto;
use std::fs;
use tokio::sync::mpsc::channel;
use tokio::time::{timeout, Duration};

fn digest_from_bytes(bytes: &[u8]) -> Digest {
    Digest(Sha512::digest(bytes).as_slice()[..32].try_into().unwrap())
}

#[tokio::test]
async fn missing_payload_without_inline_triggers_sync() {
    let mut keys = keys();
    let _ = keys.pop().unwrap(); // Skip the header author.
    let (name, _) = keys.pop().unwrap();

    let (tx_header_waiter, mut rx_header_waiter) = channel(1);
    let (tx_certificate_waiter, _rx_certificate_waiter) = channel(1);

    let path = ".db_test_missing_payload_without_inline_triggers_sync";
    let _ = fs::remove_dir_all(path);
    let store = Store::new(path).unwrap();

    let mut synchronizer = Synchronizer::new(
        name,
        &committee(),
        store,
        tx_header_waiter,
        tx_certificate_waiter,
    );

    let batch = b"missing-inline".to_vec();
    let digest = digest_from_bytes(&batch);
    let mut header = header();
    header.payload = [(digest.clone(), 0)].iter().cloned().collect();
    header.inline_payload = None;
    header.id = header.digest();

    let missing = synchronizer.missing_payload(&header).await.unwrap();
    assert!(missing);

    let waiter_message = timeout(Duration::from_secs(1), rx_header_waiter.recv())
        .await
        .expect("Expected sync request")
        .expect("Header waiter channel closed");

    match waiter_message {
        WaiterMessage::SyncBatches(missing, delivered_header) => {
            assert_eq!(missing.get(&digest), Some(&0));
            assert_eq!(delivered_header.id, header.id);
        }
        other => panic!("Unexpected waiter message: {:?}", other),
    }
}

#[tokio::test]
async fn inline_payload_skips_sync_batches() {
    let mut keys = keys();
    let _ = keys.pop().unwrap(); // Skip the header author.
    let (name, _) = keys.pop().unwrap();

    let (tx_header_waiter, mut rx_header_waiter) = channel(1);
    let (tx_certificate_waiter, _rx_certificate_waiter) = channel(1);

    let path = ".db_test_inline_payload_skips_sync_batches";
    let _ = fs::remove_dir_all(path);
    let store = Store::new(path).unwrap();

    let mut synchronizer = Synchronizer::new(
        name,
        &committee(),
        store,
        tx_header_waiter,
        tx_certificate_waiter,
    );

    let batch = b"inline-available".to_vec();
    let digest = digest_from_bytes(&batch);
    let mut header = header();
    header.payload = [(digest.clone(), 0)].iter().cloned().collect();
    header.inline_payload = Some([(digest.clone(), batch)].iter().cloned().collect());
    header.id = header.digest();

    let missing = synchronizer.missing_payload(&header).await.unwrap();
    assert!(!missing);

    let no_waiter_message = timeout(Duration::from_millis(100), rx_header_waiter.recv()).await;
    assert!(no_waiter_message.is_err(), "Did not expect sync request");
}

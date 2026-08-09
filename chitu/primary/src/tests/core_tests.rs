// Copyright(C) Facebook, Inc. and its affiliates.
use super::*;
use crate::common::{
    certificate, committee, committee_with_base_port, header, headers, keys, listener, votes,
};
use crate::messages::HeaderBundle;
use crypto::Signature;
use std::{collections::HashSet, fs};
use tokio::sync::mpsc::channel;

#[tokio::test]
async fn process_header() {
    let mut keys = keys();
    let _ = keys.pop().unwrap(); // Skip the header' author.
    let (name, secret) = keys.pop().unwrap();
    let mut signature_service = SignatureService::new(secret);

    let committee = committee_with_base_port(13_000);

    let (tx_sync_headers, _rx_sync_headers) = channel(1);
    let (tx_sync_certificates, _rx_sync_certificates) = channel(1);
    let (tx_primary_messages, rx_primary_messages) = channel(1);
    let (_tx_headers_loopback, rx_headers_loopback) = channel(1);
    let (_tx_certificates_loopback, rx_certificates_loopback) = channel(1);
    let (_tx_headers, rx_headers) = channel(1);
    let (tx_consensus, _rx_consensus) = channel(1);
    let (tx_parents, _rx_parents) = channel(1);

    // Create a new test store.
    let path = ".db_test_process_header";
    let _ = fs::remove_dir_all(path);
    let mut store = Store::new(path).unwrap();

    // Make the vote we expect to receive.
    let expected = Vote::new(&header(), &name, &mut signature_service).await;

    // Spawn a listener to receive the vote.
    let address = committee
        .primary(&header().author)
        .unwrap()
        .primary_to_primary;
    let handle = listener(address);

    // Make a synchronizer for the core.
    let synchronizer = Synchronizer::new(
        name,
        &committee,
        store.clone(),
        /* tx_header_waiter */ tx_sync_headers,
        /* tx_certificate_waiter */ tx_sync_certificates,
    );

    // Spawn the core.
    Core::spawn(
        name,
        committee,
        store.clone(),
        synchronizer,
        signature_service,
        /* consensus_round */ Arc::new(AtomicU64::new(0)),
        /* gc_depth */ 50,
        /* adaptive_wait_enabled */ true,
        /* rx_primaries */ rx_primary_messages,
        /* rx_header_waiter */ rx_headers_loopback,
        /* rx_certificate_waiter */ rx_certificates_loopback,
        /* rx_proposer */ rx_headers,
        tx_consensus,
        /* tx_proposer */ tx_parents,
    );

    // Send a header to the core.
    tx_primary_messages
        .send(PrimaryMessage::Header(header()))
        .await
        .unwrap();

    // Ensure the listener correctly received the vote.
    let received = handle.await.unwrap();
    match bincode::deserialize(&received).unwrap() {
        PrimaryMessage::Vote(x) => assert_eq!(x, expected),
        x => panic!("Unexpected message: {:?}", x),
    }

    // Ensure the header is correctly stored.
    let stored = store
        .read(header().id.to_vec())
        .await
        .unwrap()
        .map(|x| bincode::deserialize(&x).unwrap());
    assert_eq!(stored, Some(header()));
}

#[tokio::test]
async fn process_header_missing_parent() {
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let (tx_sync_headers, _rx_sync_headers) = channel(1);
    let (tx_sync_certificates, _rx_sync_certificates) = channel(1);
    let (tx_primary_messages, rx_primary_messages) = channel(1);
    let (_tx_headers_loopback, rx_headers_loopback) = channel(1);
    let (_tx_certificates_loopback, rx_certificates_loopback) = channel(1);
    let (_tx_headers, rx_headers) = channel(1);
    let (tx_consensus, _rx_consensus) = channel(1);
    let (tx_parents, _rx_parents) = channel(1);

    // Create a new test store.
    let path = ".db_test_process_header_missing_parent";
    let _ = fs::remove_dir_all(path);
    let mut store = Store::new(path).unwrap();

    // Make a synchronizer for the core.
    let synchronizer = Synchronizer::new(
        name,
        &committee(),
        store.clone(),
        /* tx_header_waiter */ tx_sync_headers,
        /* tx_certificate_waiter */ tx_sync_certificates,
    );

    // Spawn the core.
    Core::spawn(
        name,
        committee(),
        store.clone(),
        synchronizer,
        signature_service,
        /* consensus_round */ Arc::new(AtomicU64::new(0)),
        /* gc_depth */ 50,
        /* adaptive_wait_enabled */ true,
        /* rx_primaries */ rx_primary_messages,
        /* rx_header_waiter */ rx_headers_loopback,
        /* rx_certificate_waiter */ rx_certificates_loopback,
        /* rx_proposer */ rx_headers,
        tx_consensus,
        /* tx_proposer */ tx_parents,
    );

    // Send a header to the core.
    let header = Header {
        parents: [Digest::default()].iter().cloned().collect(),
        ..header()
    };
    let id = header.id.clone();
    tx_primary_messages
        .send(PrimaryMessage::Header(header))
        .await
        .unwrap();

    // Ensure the header is not stored.
    assert!(store.read(id.to_vec()).await.unwrap().is_none());
}

#[tokio::test]
async fn process_header_bundle_supplies_missing_parents() {
    let mut all_keys = keys();
    let (header_author, header_secret) = all_keys.pop().unwrap();
    let (name, secret) = all_keys.pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let committee = committee_with_base_port(13_500);

    let (tx_sync_headers, _rx_sync_headers) = channel(1);
    let (tx_sync_certificates, _rx_sync_certificates) = channel(1);
    let (tx_primary_messages, rx_primary_messages) = channel(8);
    let (_tx_headers_loopback, rx_headers_loopback) = channel(1);
    let (_tx_certificates_loopback, rx_certificates_loopback) = channel(1);
    let (_tx_headers, rx_headers) = channel(1);
    let (tx_consensus, mut rx_consensus) = channel(8);
    let (tx_parents, _rx_parents) = channel(2);

    let path = ".db_test_process_header_bundle_supplies_missing_parents";
    let _ = fs::remove_dir_all(path);
    let mut store = Store::new(path).unwrap();

    let synchronizer = Synchronizer::new(
        name,
        &committee,
        store.clone(),
        tx_sync_headers,
        tx_sync_certificates,
    );

    Core::spawn(
        name,
        committee.clone(),
        store.clone(),
        synchronizer,
        signature_service,
        Arc::new(AtomicU64::new(0)),
        50,
        true,
        rx_primary_messages,
        rx_headers_loopback,
        rx_certificates_loopback,
        rx_headers,
        tx_consensus,
        tx_parents,
    );

    let parent_certificates: Vec<_> = headers()
        .into_iter()
        .take(3)
        .map(|parent_header| certificate(&parent_header))
        .collect();
    let bundled_header = {
        let header = Header {
            author: header_author,
            round: 2,
            parents: parent_certificates.iter().map(|certificate| certificate.digest()).collect(),
            ..Header::default()
        };
        Header {
            id: header.digest(),
            signature: Signature::new(&header.digest(), &header_secret),
            ..header
        }
    };

    tx_primary_messages
        .send(PrimaryMessage::HeaderBundle(HeaderBundle {
            header: bundled_header.clone(),
            parent_certificates: parent_certificates.clone(),
        }))
        .await
        .unwrap();

    let mut delivered_parent_ids = HashSet::new();
    for _ in 0..parent_certificates.len() {
        let delivered = tokio::time::timeout(
            tokio::time::Duration::from_millis(300),
            rx_consensus.recv(),
        )
        .await
        .expect("bundled parent certificate was not delivered in time")
        .unwrap();
        delivered_parent_ids.insert(delivered.header.id);
    }
    let expected_parent_ids: HashSet<_> = parent_certificates
        .iter()
        .map(|certificate| certificate.header.id.clone())
        .collect();
    assert_eq!(delivered_parent_ids, expected_parent_ids);

    let stored_header = tokio::time::timeout(tokio::time::Duration::from_millis(300), async {
        loop {
            if let Some(bytes) = store.read(bundled_header.id.to_vec()).await.unwrap() {
                break bytes;
            }
            tokio::time::sleep(tokio::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("header bundle did not make the header locally usable");
    let stored_header: Header = bincode::deserialize(&stored_header).unwrap();
    assert_eq!(stored_header, bundled_header);
}

#[tokio::test]
async fn process_header_missing_payload() {
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let (tx_sync_headers, _rx_sync_headers) = channel(1);
    let (tx_sync_certificates, _rx_sync_certificates) = channel(1);
    let (tx_primary_messages, rx_primary_messages) = channel(1);
    let (_tx_headers_loopback, rx_headers_loopback) = channel(1);
    let (_tx_certificates_loopback, rx_certificates_loopback) = channel(1);
    let (_tx_headers, rx_headers) = channel(1);
    let (tx_consensus, _rx_consensus) = channel(1);
    let (tx_parents, _rx_parents) = channel(1);

    // Create a new test store.
    let path = ".db_test_process_header_missing_payload";
    let _ = fs::remove_dir_all(path);
    let mut store = Store::new(path).unwrap();

    // Make a synchronizer for the core.
    let synchronizer = Synchronizer::new(
        name,
        &committee(),
        store.clone(),
        /* tx_header_waiter */ tx_sync_headers,
        /* tx_certificate_waiter */ tx_sync_certificates,
    );

    // Spawn the core.
    Core::spawn(
        name,
        committee(),
        store.clone(),
        synchronizer,
        signature_service,
        /* consensus_round */ Arc::new(AtomicU64::new(0)),
        /* gc_depth */ 50,
        /* adaptive_wait_enabled */ true,
        /* rx_primaries */ rx_primary_messages,
        /* rx_header_waiter */ rx_headers_loopback,
        /* rx_certificate_waiter */ rx_certificates_loopback,
        /* rx_proposer */ rx_headers,
        tx_consensus,
        /* tx_proposer */ tx_parents,
    );

    // Send a header to the core.
    let header = Header {
        payload: [(Digest::default(), 0)].iter().cloned().collect(),
        ..header()
    };
    let id = header.id.clone();
    tx_primary_messages
        .send(PrimaryMessage::Header(header))
        .await
        .unwrap();

    // Ensure the header is not stored.
    assert!(store.read(id.to_vec()).await.unwrap().is_none());
}

#[tokio::test]
async fn process_votes() {
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let committee = committee_with_base_port(13_100);

    let (tx_sync_headers, _rx_sync_headers) = channel(1);
    let (tx_sync_certificates, _rx_sync_certificates) = channel(1);
    let (tx_primary_messages, rx_primary_messages) = channel(1);
    let (_tx_headers_loopback, rx_headers_loopback) = channel(1);
    let (_tx_certificates_loopback, rx_certificates_loopback) = channel(1);
    let (tx_headers, rx_headers) = channel(1);
    let (tx_consensus, mut rx_consensus) = channel(1);
    let (tx_parents, _rx_parents) = channel(1);

    // Create a new test store.
    let path = ".db_test_process_vote";
    let _ = fs::remove_dir_all(path);
    let store = Store::new(path).unwrap();

    // Make a synchronizer for the core.
    let synchronizer = Synchronizer::new(
        name,
        &committee,
        store.clone(),
        /* tx_header_waiter */ tx_sync_headers,
        /* tx_certificate_waiter */ tx_sync_certificates,
    );

    // Spawn the core.
    Core::spawn(
        name,
        committee.clone(),
        store.clone(),
        synchronizer,
        signature_service,
        /* consensus_round */ Arc::new(AtomicU64::new(0)),
        /* gc_depth */ 50,
        /* adaptive_wait_enabled */ true,
        /* rx_primaries */ rx_primary_messages,
        /* rx_header_waiter */ rx_headers_loopback,
        /* rx_certificate_waiter */ rx_certificates_loopback,
        /* rx_proposer */ rx_headers,
        tx_consensus,
        /* tx_proposer */ tx_parents,
    );

    // First inject a locally proposed header so the core can aggregate votes for it.
    let known_header = header();
    let expected = certificate(&known_header);
    tx_headers.send(known_header.clone()).await.unwrap();

    // Send enough remote votes to reach quorum together with the local vote.
    for vote in votes(&known_header)
        .into_iter()
        .filter(|vote| vote.author != name)
        .take(2)
    {
        tx_primary_messages
            .send(PrimaryMessage::Vote(vote))
            .await
            .unwrap();
    }

    // Ensure the certificate is assembled locally and forwarded to consensus.
    let received = rx_consensus.recv().await.unwrap();
    assert_eq!(received, expected);
}

#[tokio::test]
async fn process_certificates() {
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let (tx_sync_headers, _rx_sync_headers) = channel(1);
    let (tx_sync_certificates, _rx_sync_certificates) = channel(1);
    let (tx_primary_messages, rx_primary_messages) = channel(3);
    let (_tx_headers_loopback, rx_headers_loopback) = channel(1);
    let (_tx_certificates_loopback, rx_certificates_loopback) = channel(1);
    let (_tx_headers, rx_headers) = channel(1);
    let (tx_consensus, mut rx_consensus) = channel(3);
    let (tx_parents, mut rx_parents) = channel(1);

    // Create a new test store.
    let path = ".db_test_process_certificates";
    let _ = fs::remove_dir_all(path);
    let mut store = Store::new(path).unwrap();

    // Make a synchronizer for the core.
    let synchronizer = Synchronizer::new(
        name,
        &committee(),
        store.clone(),
        /* tx_header_waiter */ tx_sync_headers,
        /* tx_certificate_waiter */ tx_sync_certificates,
    );

    // Spawn the core.
    Core::spawn(
        name,
        committee(),
        store.clone(),
        synchronizer,
        signature_service,
        /* consensus_round */ Arc::new(AtomicU64::new(0)),
        /* gc_depth */ 50,
        /* adaptive_wait_enabled */ true,
        /* rx_primaries */ rx_primary_messages,
        /* rx_header_waiter */ rx_headers_loopback,
        /* rx_certificate_waiter */ rx_certificates_loopback,
        /* rx_proposer */ rx_headers,
        tx_consensus,
        /* tx_proposer */ tx_parents,
    );

    // Send enough certificates to the core.
    let certificates: Vec<_> = headers()
        .iter()
        .take(3)
        .map(|header| certificate(header))
        .collect();

    for x in certificates.clone() {
        tx_primary_messages
            .send(PrimaryMessage::Certificate(x))
            .await
            .unwrap();
    }

    // Ensure the core sends the parents of the certificates to the proposer.
    let received = rx_parents.recv().await.unwrap();
    let received_parents: HashSet<_> = received.0.parents.into_iter().collect();
    let expected_parents: HashSet<_> = certificates.iter().map(|x| x.digest()).collect();
    assert_eq!(received.1, 1);
    assert_eq!(received_parents, expected_parents);

    // Ensure the core sends the certificates to the consensus.
    for x in certificates.clone() {
        let received = rx_consensus.recv().await.unwrap();
        assert_eq!(received, x);
    }

    // Ensure the certificates are stored.
    for x in &certificates {
        let stored = store.read(x.digest().to_vec()).await.unwrap();
        let serialized = bincode::serialize(x).unwrap();
        assert_eq!(stored, Some(serialized));
    }
}

#[tokio::test]
async fn adaptive_wait_absorbs_late_certificate() {
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let (tx_sync_headers, _rx_sync_headers) = channel(1);
    let (tx_sync_certificates, _rx_sync_certificates) = channel(1);
    let (tx_primary_messages, rx_primary_messages) = channel(4);
    let (_tx_headers_loopback, rx_headers_loopback) = channel(1);
    let (_tx_certificates_loopback, rx_certificates_loopback) = channel(1);
    let (_tx_headers, rx_headers) = channel(1);
    let (tx_consensus, _rx_consensus) = channel(4);
    let (tx_parents, mut rx_parents) = channel(2);

    let path = ".db_test_adaptive_wait_absorbs_late_certificate";
    let _ = fs::remove_dir_all(path);
    let store = Store::new(path).unwrap();

    let synchronizer = Synchronizer::new(
        name,
        &committee(),
        store.clone(),
        tx_sync_headers,
        tx_sync_certificates,
    );

    Core::spawn(
        name,
        committee(),
        store,
        synchronizer,
        signature_service,
        Arc::new(AtomicU64::new(0)),
        50,
        true,
        rx_primary_messages,
        rx_headers_loopback,
        rx_certificates_loopback,
        rx_headers,
        tx_consensus,
        tx_parents,
    );

    let certificates: Vec<_> = headers()
        .iter()
        .take(4)
        .map(|header| certificate(header))
        .collect();

    tx_primary_messages
        .send(PrimaryMessage::Header(certificates[3].header.clone()))
        .await
        .unwrap();
    for vote in votes(&certificates[3].header).into_iter().take(1) {
        tx_primary_messages
            .send(PrimaryMessage::Vote(vote))
            .await
            .unwrap();
    }

    tx_primary_messages
        .send(PrimaryMessage::Certificate(certificates[0].clone()))
        .await
        .unwrap();
    tx_primary_messages
        .send(PrimaryMessage::Certificate(certificates[1].clone()))
        .await
        .unwrap();
    tx_primary_messages
        .send(PrimaryMessage::Certificate(certificates[2].clone()))
        .await
        .unwrap();

    tokio::time::sleep(tokio::time::Duration::from_millis(5)).await;

    tx_primary_messages
        .send(PrimaryMessage::Certificate(certificates[3].clone()))
        .await
        .unwrap();

    let received = tokio::time::timeout(
        tokio::time::Duration::from_millis(200),
        rx_parents.recv(),
    )
    .await
    .expect("adaptive wait did not release in time")
    .unwrap();

    let received_parents: HashSet<_> = received.0.parents.into_iter().collect();
    let expected_parents: HashSet<_> = certificates.iter().map(|x| x.digest()).collect();
    assert_eq!(received.1, 1);
    assert_eq!(received_parents, expected_parents);
}

#[tokio::test]
async fn adaptive_wait_can_be_disabled() {
    let (name, secret) = keys().pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let (tx_sync_headers, _rx_sync_headers) = channel(1);
    let (tx_sync_certificates, _rx_sync_certificates) = channel(1);
    let (tx_primary_messages, rx_primary_messages) = channel(4);
    let (_tx_headers_loopback, rx_headers_loopback) = channel(1);
    let (_tx_certificates_loopback, rx_certificates_loopback) = channel(1);
    let (_tx_headers, rx_headers) = channel(1);
    let (tx_consensus, _rx_consensus) = channel(4);
    let (tx_parents, mut rx_parents) = channel(2);

    let path = ".db_test_adaptive_wait_can_be_disabled";
    let _ = fs::remove_dir_all(path);
    let store = Store::new(path).unwrap();

    let synchronizer = Synchronizer::new(
        name,
        &committee(),
        store.clone(),
        tx_sync_headers,
        tx_sync_certificates,
    );

    Core::spawn(
        name,
        committee(),
        store,
        synchronizer,
        signature_service,
        Arc::new(AtomicU64::new(0)),
        50,
        false,
        rx_primary_messages,
        rx_headers_loopback,
        rx_certificates_loopback,
        rx_headers,
        tx_consensus,
        tx_parents,
    );

    let certificates: Vec<_> = headers()
        .iter()
        .take(4)
        .map(|header| certificate(header))
        .collect();

    tx_primary_messages
        .send(PrimaryMessage::Certificate(certificates[0].clone()))
        .await
        .unwrap();
    tx_primary_messages
        .send(PrimaryMessage::Certificate(certificates[1].clone()))
        .await
        .unwrap();
    tx_primary_messages
        .send(PrimaryMessage::Certificate(certificates[2].clone()))
        .await
        .unwrap();

    let received = tokio::time::timeout(
        tokio::time::Duration::from_millis(200),
        rx_parents.recv(),
    )
    .await
    .expect("disabled adaptive wait did not release immediately")
    .unwrap();

    let received_parents: HashSet<_> = received.0.parents.into_iter().collect();
    let expected_parents: HashSet<_> =
        certificates[..3].iter().map(|x| x.digest()).collect();
    assert_eq!(received.1, 1);
    assert_eq!(received_parents, expected_parents);
}

#[tokio::test]
async fn process_votes_for_known_remote_header() {
    let mut all_keys = keys();
    let (header_author, header_secret) = all_keys.pop().unwrap();
    let (name, secret) = all_keys.pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let committee = committee_with_base_port(13_300);

    let (tx_sync_headers, _rx_sync_headers) = channel(1);
    let (tx_sync_certificates, _rx_sync_certificates) = channel(1);
    let (tx_primary_messages, rx_primary_messages) = channel(8);
    let (_tx_headers_loopback, rx_headers_loopback) = channel(1);
    let (_tx_certificates_loopback, rx_certificates_loopback) = channel(1);
    let (_tx_headers, rx_headers) = channel(1);
    let (tx_consensus, mut rx_consensus) = channel(8);
    let (tx_parents, _rx_parents) = channel(1);

    let path = ".db_test_process_votes_for_known_remote_header";
    let _ = fs::remove_dir_all(path);
    let store = Store::new(path).unwrap();

    let synchronizer = Synchronizer::new(
        name,
        &committee,
        store.clone(),
        tx_sync_headers,
        tx_sync_certificates,
    );

    Core::spawn(
        name,
        committee.clone(),
        store,
        synchronizer,
        signature_service,
        Arc::new(AtomicU64::new(0)),
        50,
        true,
        rx_primary_messages,
        rx_headers_loopback,
        rx_certificates_loopback,
        rx_headers,
        tx_consensus,
        tx_parents,
    );

    let remote_header = {
        let header = Header {
            author: header_author,
            round: 1,
            parents: Certificate::genesis(&committee)
                .iter()
                .map(|x| x.digest())
                .collect(),
            ..Header::default()
        };
        Header {
            id: header.digest(),
            signature: Signature::new(&header.digest(), &header_secret),
            ..header
        }
    };

    tx_primary_messages
        .send(PrimaryMessage::Header(remote_header.clone()))
        .await
        .unwrap();

    for vote in votes(&remote_header) {
        tx_primary_messages
            .send(PrimaryMessage::Vote(vote))
            .await
            .unwrap();
    }

    let delivered = tokio::time::timeout(
        tokio::time::Duration::from_millis(300),
        rx_consensus.recv(),
    )
    .await
    .expect("remote header did not get certified in time")
    .unwrap();
    assert_eq!(delivered.header.id, remote_header.id);
}

#[tokio::test]
async fn sync_weak_certificate_replays_votes() {
    let mut all_keys = keys();
    let (header_author, header_secret) = all_keys.pop().unwrap();
    let (name, secret) = all_keys.pop().unwrap();
    let signature_service = SignatureService::new(secret);

    let committee = committee_with_base_port(13_400);

    let (tx_sync_headers, _rx_sync_headers) = channel(1);
    let (tx_sync_certificates, _rx_sync_certificates) = channel(1);
    let (tx_primary_messages, rx_primary_messages) = channel(8);
    let (_tx_headers_loopback, rx_headers_loopback) = channel(1);
    let (_tx_certificates_loopback, rx_certificates_loopback) = channel(1);
    let (_tx_headers, rx_headers) = channel(1);
    let (tx_consensus, mut rx_consensus) = channel(8);
    let (tx_parents, _rx_parents) = channel(1);

    let path = ".db_test_sync_weak_certificate_replays_votes";
    let _ = fs::remove_dir_all(path);
    let store = Store::new(path).unwrap();

    let synchronizer = Synchronizer::new(
        name,
        &committee,
        store.clone(),
        tx_sync_headers,
        tx_sync_certificates,
    );

    Core::spawn(
        name,
        committee.clone(),
        store,
        synchronizer,
        signature_service,
        Arc::new(AtomicU64::new(0)),
        50,
        true,
        rx_primary_messages,
        rx_headers_loopback,
        rx_certificates_loopback,
        rx_headers,
        tx_consensus,
        tx_parents,
    );

    let remote_header = {
        let header = Header {
            author: header_author,
            round: 1,
            parents: Certificate::genesis(&committee)
                .iter()
                .map(|x| x.digest())
                .collect(),
            ..Header::default()
        };
        Header {
            id: header.digest(),
            signature: Signature::new(&header.digest(), &header_secret),
            ..header
        }
    };

    let weak_certificate = Certificate {
        header: remote_header.clone(),
        votes: votes(&remote_header)
            .into_iter()
            .filter(|vote| vote.author != name)
            .take(2)
            .map(|vote| (vote.author, vote.signature))
            .collect(),
    };

    tx_primary_messages
        .send(PrimaryMessage::SyncWeakCertificate(weak_certificate))
        .await
        .unwrap();

    let delivered = tokio::time::timeout(
        tokio::time::Duration::from_millis(300),
        rx_consensus.recv(),
    )
    .await
    .expect("weak certificate did not trigger certification in time")
    .unwrap();
    assert_eq!(delivered.header.id, remote_header.id);
}

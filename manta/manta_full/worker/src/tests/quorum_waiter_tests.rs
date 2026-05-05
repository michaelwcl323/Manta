use super::*;
use crate::common::{batch, committee_with_base_port, keys, listener};
use crate::worker::WorkerMessage;
use bytes::Bytes;
use futures::future::try_join_all;
use network::ReliableSender;
use tokio::sync::mpsc::channel;
use tokio::time::{timeout, Duration};

#[tokio::test]
async fn wait_for_quorum() {
    let (tx_message, rx_message) = channel(1);
    let (tx_batch, mut rx_batch) = channel(1);
    let (myself, _) = keys().pop().unwrap();
    let committee = committee_with_base_port(7_000);

    // Spawn a `QuorumWaiter` instance.
    QuorumWaiter::spawn(committee.clone(), /* stake */ 1, rx_message, tx_batch);

    // Make a batch.
    let message = WorkerMessage::Batch(batch());
    let serialized = bincode::serialize(&message).unwrap();
    let expected = Bytes::from(serialized.clone());

    // Spawn enough listeners to acknowledge our batches.
    let mut names = Vec::new();
    let mut addresses = Vec::new();
    let mut listener_handles = Vec::new();
    for (name, address) in committee.others_workers(&myself, /* id */ &0) {
        let address = address.worker_to_worker;
        let handle = listener(address, Some(expected.clone()));
        names.push(name);
        addresses.push(address);
        listener_handles.push(handle);
    }

    // Broadcast the batch through the network.
    let bytes = Bytes::from(serialized.clone());
    let handlers = ReliableSender::new().broadcast(addresses, bytes).await;

    // Forward the batch along with the handlers to the `QuorumWaiter`.
    let message = QuorumWaiterMessage {
        batch: serialized.clone(),
        handlers: names.into_iter().zip(handlers.into_iter()).collect(),
    };
    tx_message.send(message).await.unwrap();

    // Wait for the `QuorumWaiter` to gather enough acknowledgements and output the batch.
    let output = rx_batch.recv().await.unwrap();
    assert_eq!(output, serialized);

    // Ensure the other listeners correctly received the batch.
    assert!(try_join_all(listener_handles).await.is_ok());
}

#[tokio::test]
async fn wait_for_full_quorum() {
    let (tx_message, rx_message) = channel(1);
    let (tx_batch, mut rx_batch) = channel(1);
    let (myself, _) = keys().pop().unwrap();
    let committee = committee_with_base_port(7_100);

    // Spawn a `QuorumWaiter` instance.
    QuorumWaiter::spawn(committee.clone(), /* stake */ 1, rx_message, tx_batch);

    // Make a batch.
    let message = WorkerMessage::Batch(batch());
    let serialized = bincode::serialize(&message).unwrap();
    let expected = Bytes::from(serialized.clone());

    // Spawn only one listener. With 4 equally weighted authorities, our own stake plus one
    // acknowledgement only reaches f+1 and must not release the batch.
    let mut peers = committee.others_workers(&myself, /* id */ &0).into_iter();
    let (name, addresses) = peers.next().unwrap();
    let address = addresses.worker_to_worker;
    let listener_handle = listener(address, Some(expected.clone()));

    // Broadcast the batch to a single peer.
    let bytes = Bytes::from(serialized.clone());
    let handler = ReliableSender::new().send(address, bytes).await;

    // Forward the batch along with the handler to the `QuorumWaiter`.
    let message = QuorumWaiterMessage {
        batch: serialized.clone(),
        handlers: vec![(name, handler)],
    };
    tx_message.send(message).await.unwrap();

    // A single acknowledgement is not enough to release the batch.
    assert!(timeout(Duration::from_millis(200), rx_batch.recv()).await.is_err());

    // Ensure the listener correctly received the batch.
    listener_handle.await.unwrap();
}

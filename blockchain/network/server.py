"""Python Flask server for restful communication.

This module defines the interface for the REST API for incoming requests.
Needs to be replaced, when a P2P is estabished.
"""

from flask import Flask, request
import os
from threading import Thread
from ..config import CONFIG

app = Flask(__name__)


def handle_received_block(block):
    """Handle new block in extra thread for early return."""
    full_client.received_new_block(block)


def handle_received_transaction(transaction):
    """Handle new transaction in extra thread for early return."""
    full_client.handle_incoming_transaction(transaction)


@app.route(CONFIG["ROUTES"]["new_block"], methods=["POST"])
def _new_block():
    block = request.data.decode("utf-8")
    Thread(target=handle_received_block, args=(block,), daemon=True, name="handle_received_block_thread").start()
    return "success"


@app.route(CONFIG["ROUTES"]["block_by_index"], methods=["GET"])
def _send_block_by_id(index):
    # TODO use multiple leaves
    block = full_client.chain.find_blocks_by_index(int(index))[0]
    return repr(block)


@app.route(CONFIG["ROUTES"]["block_by_hash"], methods=["GET"])
def _send_block_by_hash(hash):
    block = full_client.chain.find_block_by_hash(hash)
    return repr(block)


@app.route(CONFIG["ROUTES"]["new_transaction"], methods=["POST"])
def _new_transaction():
    new_transaction = request.data
    Thread(target=handle_received_transaction, args=(new_transaction,), daemon=True, name="handle_received_tx_thread").start()
    return "success"


@app.route(CONFIG["ROUTES"]["latest_block"], methods=["GET"])
def _latest_block():
    block = full_client.chain.last_block()
    return repr(block)


def start_server(client):
    """Start the flask server."""
    global full_client
    full_client = client
    port = CONFIG["default_port"]
    if os.getenv("SERVER_PORT"):
        port = int(os.getenv("SERVER_PORT"))
    app.run(host="0.0.0.0", port=port)

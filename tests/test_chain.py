import shutil

from blockchain.chain import Chain
from blockchain.block import Block
from blockchain.config import CONFIG
from blockchain.helper.key_utils import load_rsa_from_pem

import pytest
import os

from tests.config_fixture import setup_test_config
setup_test_config()

PUBLIC_KEY = load_rsa_from_pem("tests" + os.sep + "testkey_pub.bin")
PRIVATE_KEY = load_rsa_from_pem("tests" + os.sep + "testkey_priv.bin")


def setup_module(module):
    shutil.rmtree(CONFIG.persistance_folder)
    os.makedirs(CONFIG.persistance_folder)


def test_chain_is_singleton():
    chain_1 = Chain(load_persisted=False)
    chain_2 = Chain(load_persisted=False)
    assert id(chain_1) == id(chain_2)


@pytest.fixture()
def chain():
    chain = Chain(load_persisted=False)
    yield chain


@pytest.fixture()
def next_block(chain):
    block_information = chain.genesis_block.get_block_information()
    next_block = Block(block_information, PUBLIC_KEY)
    next_block.sign(PRIVATE_KEY)
    next_block.update_hash()
    yield next_block


def test_find_block_by_hash(chain, next_block):
    chain.add_block(next_block)
    hash = next_block.hash
    assert chain.find_block_by_hash(hash) == next_block
    assert chain.find_block_by_hash("some random hash") is None


def teardown_module(module):
    shutil.rmtree(CONFIG.persistance_folder)
    os.makedirs(CONFIG.persistance_folder)

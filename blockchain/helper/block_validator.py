"""This module handles the block validation."""

from Crypto.PublicKey import RSA
import logging
from time import time
from hashlib import sha256

from ..helper.cryptography import verify
from ..config import CONFIG

logger = logging.getLogger("block-validator")


def validate_block(block, previous_block):
    """Validate correctness of block by defined rules."""
    # Index is incremented by 1
    if block.index != previous_block.index + 1:
        logger.info("Wrong index, block index {}, index of last block {}"
                    .format(block.index, previous_block.index))
        return False

    # New block references old one
    if block.previous_block != previous_block.hash:
        logger.info(("New block does not reference previous one, block hash"
                     " {}, hash of last block {}")
                    .format(block.hash, previous_block.hash))
        return False

    # Matching versions
    if block.version != CONFIG["version"]:
        logger.info("Different versions, block version {}, chain version {}"
                    .format(block.version, CONFIG["version"]))
        return False

    # Timestamp not in the future
    if block.timestamp > int(time()):
        # TODO deviation in the past ??
        logger.info("Timestamp of the new block is in the future")
        return False

    # TODO
    # if block.public_key not in admission nodes?

    # Valid signature
    content_to_sign = str.encode(block.get_content_for_signing())
    signature = bytes.fromhex(block.signature)
    public_key = RSA.importKey(bytes.fromhex(block.public_key))
    valid = verify(content_to_sign, signature, public_key)
    if not valid:
        logger.info("Signature is not valid, block must be altered")
        return False

    # Number of transactions does not exceed the maximum
    if len(block.transactions) > CONFIG["block_size"]:
        logger.info("Too many transactions, block has {}, maximum is {}"
                    .format(len(block.transactions), CONFIG["block_size"]))
        return False

    # TODO too few transactions??

    # Duplicate transactions
    if len(block.transactions) > len(set([repr(tx) for tx in block.transactions])):
        logger.info("Block contains duplicate transactions")
        return False

    # Block hash valid
    content_to_hash = block.get_content_for_hashing()
    sha = sha256()
    sha.update(content_to_hash.encode("utf-8"))
    if block.hash != sha.hexdigest():
        logger.info("Hash is not valid, block must be altered")
        return False

    return True

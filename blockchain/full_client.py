import logging
import os
import random
import threading

import requests
import sched
import time
from threading import Thread

from .transaction_set import TransactionSet
from .block import Block
from .chain import Chain
from .config import CONFIG
from .transaction import *
from .helper.cryptography import generate_keypair
from Crypto.PublicKey import RSA

logger = logging.getLogger("client")
scheduler = sched.scheduler(time.time, time.sleep)


class FullClient(object):
    """docstring for FullClient"""
    def __init__(self):
        # Mock nodes by hard coding

        if os.getenv('NEIGHBORS_HOST_PORT'):
            neighbors_list = os.getenv('NEIGHBORS_HOST_PORT')
            neighbors_list = map(str.strip, neighbors_list.split(","))
            self.nodes = ["http://" + neighbor for neighbor in neighbors_list]
        else:
            self.nodes = ["http://127.0.0.1:9000"]
        self._setup_public_key()

        self.chain = Chain()
        self.transaction_set = TransactionSet()
        self.invalid_transactions = set()
        self.dangling_blocks = set()
        self.stop_creator_election = False
        self.creator_election_thread = None
        self._start_election_thread()
        self.recover_after_shutdown()

    def _start_election_thread(self):
        self.stop_creator_election = False
        self.creator_election_thread = threading.Thread(target=self.creator_election, daemon=True)
        self.creator_election_thread.start()

    def _stop_election_thread(self):
        self.stop_creator_election = True
        self.creator_election_thread.join()

    def determine_block_creation_node(self, timestamp=time.time()):
        """Determine which admission node has to create the next block in chain.

        The method takes a timestamp as argument representing the creation date of the block whose legitimate creator
        should be determined. Defaults to 'now', which means "Who should create a block right now?"
        Returns the public key of the determined creator

        If even the youngest creator failed to create a block within time, the method continues with the
        oldest submission node.
        """
        number_of_admissions = len(self.chain.get_admissions())
        creator_history = self.chain.get_block_creation_history(number_of_admissions)

        last_block_timestamp = self.chain.last_block().timestamp

        delta_time = int(timestamp) - int(last_block_timestamp)

        nth_oldest_block = int(delta_time / CONFIG["block_time"])

        return creator_history[nth_oldest_block % number_of_admissions]

    def _setup_public_key(self):
        """Create new key pair if necessary.

        Create a public/private key pair on setup and save them in files. If
        the full client restarts, file will be read in.
        """
        key_folder = CONFIG["key_folder"]
        if not os.path.isdir(key_folder) or os.listdir(key_folder) == []:
            # No keys present, so generate new pair
            os.makedirs(CONFIG["key_folder"], exist_ok=True)

            logger.info("Generating new public/private key pair")
            self.public_key, self.private_key = generate_keypair()

            path = os.path.join(key_folder, CONFIG["key_file_names"][0])
            with open(path, "wb") as key_file:
                key_file.write(self.public_key.exportKey())

            path = os.path.join(key_folder, CONFIG["key_file_names"][1])
            with open(path, "wb") as key_file:
                key_file.write(self.private_key.exportKey())

        elif set(os.listdir(key_folder)) != set(CONFIG["key_file_names"]):
            # One key is missing
            logger.error("Public or Private key are not existent!")
            assert os.listdir(key_folder) == CONFIG["key_file_names"]

        else:
            # Keys are present
            path = os.path.join(key_folder, CONFIG["key_file_names"][0])
            with open(path, "rb") as key_file:
                self.public_key = RSA.import_key(key_file.read())

            path = os.path.join(key_folder, CONFIG["key_file_names"][1])
            with open(path, "rb") as key_file:
                self.private_key = RSA.import_key(key_file.read())

    def synchronize_blockchain(self):
        random_node = random.choice(self.nodes)
        last_block_remote = self._get_status_from_different_node(random_node)
        if last_block_remote.index == self.chain.last_block().index and \
           last_block_remote.hash == self.chain.last_block().hash:
            # blockchain is up-to-date
            return
        if last_block_remote.index == self.chain.last_block().index and \
           last_block_remote.hash != self.chain.last_block().hash:
            # TODO: at least last block is wrong for self or the other node
            pass
        if last_block_remote.index > self.chain.last_block().index:
            syncing_block = self._request_block_at_index(self.chain.last_block().index + 1, random_node)
            self._add_block_if_valid(syncing_block)
            self.synchronize_blockchain()

    def create_next_block(self):
        new_block = Block(self.chain.last_block().get_block_information(),
                          self.public_key)

        for _ in range(CONFIG["block_size"]):
            if len(self.transaction_set):
                transaction = self.transaction_set.pop()
                if transaction.validate():
                    new_block.add_transaction(transaction)
                else:
                    self.invalid_transactions.add(transaction)
            else:
                # Break if transaction set is empty
                break
        new_block.sign(self.private_key)
        new_block.update_hash()
        return new_block

    def submit_block(self, block):
        self.chain.add_block(block)
        block.persist()
        self._broadcast_new_block(block)

    def received_new_block(self, block_representation):
        """This method is called when receiving a new block.

        It will check if the block was received earlier. If not it will process and broadcast the block and adding it
        to the chain or dangling blocks."""
        logger.debug("Received new block: {}".format(repr(block_representation)))
        new_block = Block(block_representation)

        if self.chain.find_block_by_hash(new_block.hash) or new_block in self.dangling_blocks:
            logger.debug("The received block is already part of chain or a dangling block: {}".format(repr(block_representation)))
            return

        self._stop_election_thread()

        self._broadcast_new_block(new_block)

        expected_pub_key = self.determine_block_creation_node(timestamp=new_block.timestamp)

        if expected_pub_key != new_block.public_key:
            logger.debug("Received block doesn't match as next block in chain. Adding it to dangling blocks: {}".format(
                repr(block_representation)))
            self.dangling_blocks.add(new_block)
        else:
            self._add_block_if_valid(new_block)

        self._start_election_thread()

    def creator_election(self):
        """This method checks if this node needs to generate a new block.

        If it is the next creator it will generate a block and submit it to the chain."""
        while not self.stop_creator_election:
            time.sleep(CONFIG["block_time"])
            next_creator = self.determine_block_creation_node()
            #TODO choose unified representation of rsa public key!
            if next_creator == self.public_key.exportKey("DER"):
                new_block = self.create_next_block()
                if not new_block.validate():
                    logger.error("New generated block is not valid! {}".format(repr(new_block)))
                self.submit_block(new_block)

    def _request_block_at_index(self, index, node):
        route = node + "/request_block/index/" + str(index)
        block = requests.get(route)
        return Block(block.text)

    def _add_block_if_valid(self, block, broadcast_block=False):
        if block.validate():
            self.chain.add_block(block)
            block.persist()
            self.process_dangling_blocks()
            if broadcast_block:
                self._broadcast_new_block(block)

    def _broadcast_new_block(self, block):
        for node in self.nodes:
            route = node + "/new_block"
            # TODO: this doesnt work, if we send it to the same node
            requests.post(route, data=repr(block), timeout=5)

    def _get_status_from_different_node(self, node):
        route = node + "/latest_block"
        block = requests.get(route)
        return Block(block.text)

    def recover_after_shutdown(self):
        # Steps:
        #   1. read in files from disk -> maybe in __init__ of chain
        #   2. sync with other node(s)
        pass

    def handle_incoming_transaction(self, transaction):
        transaction_object = eval(transaction)
        self._handle_transaction(transaction_object)

    def handle_transaction(self, transaction, broadcast=False):
        if self.transaction_set.contains(transaction):
            return  # Transaction was already received
        else:
            # TODO: check if it is in the chain already
            self.transaction_set.add(transaction)
            if broadcast:
                self._broadcast_new_transaction(transaction)

    def _broadcast_new_transaction(self, transaction):
        """Broadcast transaction to required number of admission nodes."""
        # TODO: send to admissions only
        for node in self.nodes:
            route = node + "/new_transaction"
            requests.post(route, data=repr(transaction))

    def create_transaction(self):
        transaction_type = input("What kind of transaction should be created? (Vaccination/Vaccine/Permission)").lower()
        if transaction_type == "vaccination":
            vaccine = input("Which vaccine was given?").lower()
            doctor_pubkey = eval(input("Enter doctors public key"))
            patient_pubkey = eval(input("Enter patients public key"))
            transaction = VaccinationTransaction(doctor_pubkey, patient_pubkey, vaccine)
            print(transaction)
            sign_now = input("Sign transaction now? (Y/N)").lower()
            if sign_now == "y":
                doctor_privkey = eval(input("Enter doctors private key"))
                patient_privkey = eval(input("Enter patients private key"))
                transaction.sign(doctor_privkey, patient_privkey)
                print(transaction)
                self.handle_transaction(transaction, broadcast=True)
            elif sign_now == "n":
                print("Cannot broadcast unsigned transactions, aborting.")
            else:
                print("Invalid option {}, aborting.".format(sign_now))
        elif transaction_type == "vaccine":
            vaccine = input("Which vaccine should be registered?").lower()
            admission_pubkey = eval(input("Enter admissions public key"))
            transaction = VaccineTransaction(vaccine, admission_pubkey)
            print(transaction)
            sign_now = input("Sign transaction now? (Y/N)").lower()
            if sign_now == "y":
                admission_privkey = eval(input("Enter admission private key"))
                transaction.sign(admission_privkey)
                print(transaction)
                self._handle_transaction(transaction, broadcast=True)
            elif sign_now == "n":
                print("Cannot broadcast unsigned transactions, aborting.")
            else:
                print("Invalid option {}, aborting.".format(sign_now))
        elif transaction_type == "permission":
            permission_name = input("Which permission should be granted? (Patient/Doctor/Admission)").lower()
            permission = Permission[permission_name]
            sender_pubkey = eval(input("Enter sender public key"))
            transaction = PermissionTransaction(permission, sender_pubkey)
            print(transaction)
            sign_now = input("Sign transaction now? (Y/N)").lower()
            if sign_now == "y":
                sender_privkey = eval(input("Enter sender private key"))
                transaction.sign(sender_privkey)
                print(transaction)
                self.handle_transaction(transaction, broadcast=True)
            elif sign_now == "n":
                print("Cannot broadcast unsigned transactions, aborting.")
            else:
                print("Invalid option {}, aborting.".format(sign_now))
        else:
            print("Invalid option {}, aborting.".format(transaction_type))

    def process_dangling_blocks(self):
        latest_block = self.chain.last_block()
        for block in self.dangling_blocks:
            expected_pub_key = self.determine_block_creation_node(timestamp=block.timestamp)
            if block.previous_block == latest_block.hash and expected_pub_key == block.public_key:
                if block.validate():
                    self.chain.add_block(block)
                    block.persist()
                    self.process_dangling_blocks()
                return


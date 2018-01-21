import logging
import os
import random
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

        self.chain = Chain(self.public_key)
        self.transaction_set = TransactionSet()
        self.invalid_transactions = set()

        self.recover_after_shutdown()
        self._start_runner()

    def _start_runner(self):
        """Spawn thread for block creation."""
        thread = Thread(target=self._schedule)
        thread.start()

    def _schedule(self):
        """Start scheduler."""
        scheduler.enter(CONFIG["block_time"], 1, self._create_block, (scheduler,))
        scheduler.run()

    def _create_block(self, sc):
        """Create block and schedule next event."""
        self.determine_block_creation_node()
        scheduler.enter(CONFIG["block_time"], 1, self._create_block, (sc,))

    def determine_block_creation_node(self, timestamp=time.time()):
        """Determine which admission node has to create the next block in chain.

        The method takes a timestamp as argument representing the creation date of the block whose legitimate creator
        should be determined. Defaults to 'now', which means "Who should create a block right now?"
        Returns the public key of the determined creator

        If even the youngest creator failed to create a block within time, the method continues to return the public
        key of the youngest creator.

        If the method realize that there was opportunity to create a block with the own key, it will return the own key.
        """
        number_of_admissions = len(self.chain.get_admissions())
        creator_history = self.chain.get_oldest_blockcreator(n=number_of_admissions)

        last_block_timestamp = self.chain.last_block().timestamp

        delta_time = timestamp - last_block_timestamp

        nth_oldest_block = int(delta_time / CONFIG["block_time"])

        if nth_oldest_block >= number_of_admissions:
            nth_oldest_block = number_of_admissions - 1

        if self.public_key in creator_history[:nth_oldest_block]:
            return self.public_key

        return creator_history[nth_oldest_block]

        new_block = self.create_next_block()
        self.submit_block(new_block)

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
        # TODO: implement synchronization
        if last_block_remote.index == self.chain.last_block().index and \
           last_block_remote.hash != self.chain.last_block().hash:
            # TODO: at least last block is wrong
            pass

        if last_block_remote.index != self.chain.last_block().index:
            # TODO: chain is outdated
            pass

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
        new_block.update_hash()
        return new_block

    def submit_block(self, block):
        if block.validate():
            self.chain.add_block(block)
            block.persist()
            self._broadcast_new_block(block)
        else:
            # TODO: define behaviour
            pass

    def received_new_block(self, block_representation):
        logger.debug("Received new block: {}".format(repr(block_representation)))
        new_block = Block(block_representation)
        if new_block.validate():
            self.chain.add_block(new_block)
            self._broadcast_new_block(new_block)
            new_block.persist()

    def _broadcast_new_block(self, block):
        for node in self.nodes:
            route = node + "/new_block"
            # TODO: this doesnt work, if we send it to the same node
            # requests.post(route, data=repr(block), timeout=5)

    def _get_status_from_different_node(self, node):
        random_node = random.choice(self.nodes)
        route = random_node + "/latest_block"
        block = requests.get(route)
        return Block(block.text)

    def recover_after_shutdown(self):
        # Steps:
        #   1. read in files from disk -> maybe in __init__ of chain
        #   2. sync with other node(s)
        pass

    def handle_new_transaction(self, transaction, created_by_self):
        transaction_object = eval(transaction)
        if self.transaction_set.contains(transaction_object):
            return  # Transaction was already received
        else:
            # TODO: check if it is in the chain already
            self.transaction_set.add(transaction_object)
            if created_by_self:
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
            doctor_pubkey = input("Enter doctors public key")
            patient_pubkey = input("Enter patients public key")
            transaction = VaccinationTransaction(doctor_pubkey, patient_pubkey, vaccine)
            print(transaction)
            sign_now = input("Sign transaction now? (Y/N)").lower()
            if sign_now == "y":
                doctor_privkey = input("Enter doctors private key")
                patient_privkey = input("Enter patients private key")
                transaction.sign(doctor_privkey, patient_privkey)
                print(transaction)
                return transaction
            elif sign_now == "n":
                return transaction
            else:
                print("Invalid option {}, aborting.".format(sign_now))
        elif transaction_type == "vaccine":
            vaccine = input("Which vaccine should be registered?").lower()
            admission_pubkey = input("Enter admissions public key")
            transaction = VaccineTransaction(vaccine, admission_pubkey)
            print(transaction)
            sign_now = input("Sign transaction now? (Y/N)").lower()
            if sign_now == "y":
                admission_privkey = input("Enter admission private key")
                transaction.sign(admission_privkey)
                print(transaction)
                return transaction
            elif sign_now == "n":
                return transaction
            else:
                print("Invalid option {}, aborting.".format(sign_now))
        elif transaction_type == "permission":
            permission_name = input("Which permission should be granted? (Patient/Doctor/Admission)").lower()
            permission = Permission[permission_name]
            sender_pubkey = input("Enter sender public key")
            transaction = PermissionTransaction(permission, sender_pubkey)
            print(transaction)
            if sign_now == "y":
                sender_privkey = input("Enter sender private key")
                transaction.sign(sender_privkey)
                print(transaction)
                return transaction
            elif sign_now == "n":
                return transaction
            else:
                print("Invalid option {}, aborting.".format(sign_now))
        else:
            print("Invalid option {}, aborting.".format(transaction_type))

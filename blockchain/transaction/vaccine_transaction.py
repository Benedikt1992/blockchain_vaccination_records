import logging
from blockchain.transaction.transaction import TransactionBase
import blockchain.helper.cryptography as crypto
import blockchain.helper.key_utils as key_utils

# Needs to be moved later
logging.basicConfig(level=logging.DEBUG,
                    format="[ %(asctime)s ] %(levelname)-7s %(name)-s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("blockchain")


class VaccineTransaction(TransactionBase):
    """This class depicts a registration of a vaccine."""

    def __init__(self, vaccine, sender_pubkey, signature=None, **kwargs):
        super(VaccineTransaction, self).__init__(
            vaccine=vaccine, signature=signature, sender_pubkey=sender_pubkey, **kwargs
        )

        self.vaccine = vaccine
        self.sender_pubkey = key_utils.cast_to_bytes(sender_pubkey)
        self.signature = signature

    def validate(self, admissions, doctors, vaccines):
        if self.sender_pubkey not in admissions:
            logger.debug("admission is not registered.")
            self.validation_text = "admission is not registered."
            return False
        return self._verify_signature()

    def _create_signature(self, private_key):
        message = crypto.get_bytes(self._get_information_for_hashing())
        return crypto.sign(message, private_key)

    def _get_information_for_hashing(self):
        string = "{}(version={}, timestamp={}, vaccine={}, sender_pub_key={})".format(
            type(self).__name__,
            self.version,
            self.timestamp,
            self.vaccine,
            self.sender_pubkey
        )
        return string

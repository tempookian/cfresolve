class DomainsColumnNotFoundError(IOError):
    """raised if a column with valid domains cannot be found"""


class DomainBlockedError(ConnectionError):
    """raised if a domain is resolved to a private ip address"""


class CSVReadError(IOError):
    """raised if error occurs in reading csv"""

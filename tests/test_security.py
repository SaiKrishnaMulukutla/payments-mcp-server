from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet

from payments_mcp.security.approvals import ApprovalSigner
from payments_mcp.security.encryption import AccountCipher
from payments_mcp.security.fingerprint import AccountFingerprinter


def test_account_parameters_are_encrypted_and_recoverable() -> None:
    cipher = AccountCipher(Fernet.generate_key().decode())
    plaintext = b'{"consumer_number":"123456"}'
    encrypted = cipher.encrypt(plaintext)
    assert plaintext not in encrypted
    assert cipher.decrypt(encrypted) == plaintext


def test_fingerprint_is_stable_and_biller_scoped() -> None:
    fingerprinter = AccountFingerprinter("f" * 48)
    params = b'{"consumer_number":"123456"}'
    first = fingerprinter.fingerprint(biller_id="biller-a", normalized_customer_params=params)
    assert first == fingerprinter.fingerprint(
        biller_id="biller-a",
        normalized_customer_params=params,
    )
    assert first != fingerprinter.fingerprint(
        biller_id="biller-b",
        normalized_customer_params=params,
    )


def test_approval_signature_binds_exact_payment_and_expiry() -> None:
    signer = ApprovalSigner("x" * 48)
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=5)
    fields = {
        "intent_id": "intent-1",
        "account_id": "account-1",
        "snapshot_hash": "a" * 64,
        "amount_paise": 250000,
        "fee_paise": 0,
        "expires_at": expires,
    }
    token = signer.sign(**fields)
    assert signer.verify(token, **fields, now=now)
    assert not signer.verify(token, **{**fields, "amount_paise": 250001}, now=now)
    assert not signer.verify(token, **fields, now=expires + timedelta(seconds=1))

from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet

from payments_mcp.security.approvals import ApprovalSigner
from payments_mcp.security.encryption import AccountCipher


def test_account_parameters_are_encrypted_and_recoverable() -> None:
    cipher = AccountCipher(Fernet.generate_key().decode())
    plaintext = b'{"consumer_number":"123456"}'
    encrypted = cipher.encrypt(plaintext)
    assert plaintext not in encrypted
    assert cipher.decrypt(encrypted) == plaintext


def test_approval_signature_binds_exact_payment() -> None:
    signer = ApprovalSigner("x" * 48)
    expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    token = signer.sign(
        intent_id="intent-1",
        account_id="account-1",
        snapshot_hash="a" * 64,
        amount_paise=250000,
        fee_paise=0,
        expires_at=expires,
    )
    assert signer.verify(token, expected_binding=token)
    assert not signer.verify(token, expected_binding=token + "tampered")

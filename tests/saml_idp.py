"""A stand-in SAML identity provider for tests: it signs real responses with xmlsec."""

import base64
import datetime as dt
import re
import zlib
from typing import Any
from urllib.parse import parse_qs, urlsplit

import xmlsec
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree

ENTITY_ID = "https://idp.example.edu/idp"
SSO_URL = "https://idp.example.edu/idp/sso"
HOST = "sympraxi.example.edu"
SP_BASE = f"http://{HOST}"
ACS = SP_BASE + "/accounts/saml/acs/"
AUDIENCE = SP_BASE + "/accounts/saml/metadata/"
NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
}


class Idp:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp.example.edu")])
        now = dt.datetime.now(dt.UTC)
        self.cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=365))
            .sign(self.key, hashes.SHA256())
        )

    @property
    def cert_pem(self) -> str:
        return self.cert.public_bytes(serialization.Encoding.PEM).decode()

    @property
    def key_pem(self) -> bytes:
        return self.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def response(
        self,
        request_id: str,
        *,
        name_id: str = "sam@example.edu",
        attributes: dict[str, str] | None = None,
        sign: bool = True,
        signer: "Idp | None" = None,
        audience: str = AUDIENCE,
        destination: str = ACS,
        recipient: str = ACS,
        issuer: str = ENTITY_ID,
        lifetime: int = 300,
        status: str = "urn:oasis:names:tc:SAML:2.0:status:Success",
        in_response_to: str | None = None,
        sign_response_not_assertion: bool = False,
    ) -> str:
        """A base64 SAML response answering `request_id`."""
        now = dt.datetime.now(dt.UTC)
        stamp = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        attributes = {"mail": "sam@example.edu"} if attributes is None else attributes
        cited = request_id if in_response_to is None else in_response_to
        values = "".join(
            f'<saml:Attribute Name="{k}"><saml:AttributeValue>{v}</saml:AttributeValue>'
            "</saml:Attribute>"
            for k, v in attributes.items()
        )
        statement = f"<saml:AttributeStatement>{values}</saml:AttributeStatement>" if values else ""
        xml = f"""<samlp:Response xmlns:samlp="{NS["samlp"]}" xmlns:saml="{NS["saml"]}"
  ID="_resp{id(self)}{request_id}" Version="2.0" IssueInstant="{stamp(now)}"
  InResponseTo="{cited}" Destination="{destination}">
  <saml:Issuer>{issuer}</saml:Issuer>
  <samlp:Status><samlp:StatusCode Value="{status}"/></samlp:Status>
  <saml:Assertion ID="_assert{id(self)}{request_id}" Version="2.0" IssueInstant="{stamp(now)}">
    <saml:Issuer>{issuer}</saml:Issuer>
    <saml:Subject>
      <saml:NameID
        Format="urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified">{name_id}</saml:NameID>
      <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
        <saml:SubjectConfirmationData NotOnOrAfter="{stamp(now + dt.timedelta(seconds=lifetime))}"
          Recipient="{recipient}" InResponseTo="{cited}"/>
      </saml:SubjectConfirmation>
    </saml:Subject>
    <saml:Conditions NotBefore="{stamp(now - dt.timedelta(seconds=60))}"
      NotOnOrAfter="{stamp(now + dt.timedelta(seconds=lifetime))}">
      <saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction>
    </saml:Conditions>
    <saml:AuthnStatement AuthnInstant="{stamp(now)}" SessionIndex="_s1">
      <saml:AuthnContext><saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:Password</saml:AuthnContextClassRef></saml:AuthnContext>
    </saml:AuthnStatement>
    {statement}
  </saml:Assertion>
</samlp:Response>"""
        root = etree.fromstring(xml.encode())
        if sign:
            target = root if sign_response_not_assertion else root.find("saml:Assertion", NS)
            (signer or self)._sign(target)
        return base64.b64encode(etree.tostring(root)).decode()

    def _sign(self, element: Any) -> None:
        signature = xmlsec.template.create(
            element, xmlsec.constants.TransformExclC14N, xmlsec.constants.TransformRsaSha256
        )
        issuer = element.find("saml:Issuer", NS)
        issuer.addnext(signature)
        reference = xmlsec.template.add_reference(
            signature, xmlsec.constants.TransformSha256, uri="#" + element.get("ID")
        )
        xmlsec.template.add_transform(reference, xmlsec.constants.TransformEnveloped)
        xmlsec.template.add_transform(reference, xmlsec.constants.TransformExclC14N)
        info = xmlsec.template.ensure_key_info(signature)
        xmlsec.template.add_x509_data(info)
        context = xmlsec.SignatureContext()
        context.key = xmlsec.Key.from_memory(self.key_pem, xmlsec.constants.KeyDataFormatPem)
        context.key.load_cert_from_memory(
            self.cert.public_bytes(serialization.Encoding.PEM), xmlsec.constants.KeyDataFormatPem
        )
        context.register_id(element, "ID")
        context.sign(signature)


def request_id_of(location: str) -> str:
    """The ID of the AuthnRequest in a redirect to the identity provider."""
    encoded = parse_qs(urlsplit(location).query)["SAMLRequest"][0]
    xml = zlib.decompress(base64.b64decode(encoded), -15).decode()
    found = re.search(r'ID="([^"]+)"', xml)
    assert found, xml
    return found.group(1)

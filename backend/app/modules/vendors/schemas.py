"""Vendor request and response schemas.

Bank details are a separate schema behind a separate permission, and the
non-privileged view shows a masked account number only. A vendor list is one of
the most-read screens in an ERP, and account numbers have no business being on
it.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.vendors.domain.enums import VendorStatus, VendorType


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


# -----------------------------------------------------------------------------
# Shared field types
# -----------------------------------------------------------------------------

# Pakistan NTN: 7 digits and a check digit, written 1234567-8.
NTN_PATTERN = r"^\d{7}-\d$"
# STRN: 13 digits.
STRN_PATTERN = r"^\d{13}$"
CNIC_PATTERN = r"^\d{5}-\d{7}-\d$"

Code = Annotated[str, Field(min_length=2, max_length=30, pattern=r"^[A-Z0-9][A-Z0-9\-_/]*$")]


class AddressIn(ApiModel):
    line1: Annotated[str | None, Field(max_length=200)] = None
    line2: Annotated[str | None, Field(max_length=200)] = None
    city: Annotated[str | None, Field(max_length=120)] = None
    province: Annotated[str | None, Field(max_length=120)] = None
    postal_code: Annotated[str | None, Field(max_length=20)] = None
    country: Annotated[str, Field(min_length=2, max_length=2)] = "PK"


# -----------------------------------------------------------------------------
# Contacts
# -----------------------------------------------------------------------------


class VendorContactIn(ApiModel):
    name: Annotated[str, Field(min_length=2, max_length=160)]
    designation: Annotated[str | None, Field(max_length=120)] = None
    phone: Annotated[str | None, Field(max_length=32)] = None
    alternate_phone: Annotated[str | None, Field(max_length=32)] = None
    email: Annotated[str | None, Field(max_length=160)] = None
    is_primary: bool = False
    notes: Annotated[str | None, Field(max_length=500)] = None

    @model_validator(mode="after")
    def _reachable(self) -> VendorContactIn:
        if not self.phone and not self.email:
            raise ValueError("A contact needs at least a phone number or an email address")
        return self


class VendorContactRead(ApiModel):
    id: UUID
    name: str
    designation: str | None
    phone: str | None
    alternate_phone: str | None
    email: str | None
    is_primary: bool
    notes: str | None


# -----------------------------------------------------------------------------
# Vendor
# -----------------------------------------------------------------------------


class VendorCreate(ApiModel):
    # Optional: left out, the server allocates VEN-00011 from the numbering
    # service rather than making the user invent a code.
    code: Code | None = None
    legal_name: Annotated[str, Field(min_length=2, max_length=200)]
    trade_name: Annotated[str | None, Field(max_length=200)] = None
    vendor_type: VendorType = VendorType.MATERIAL_SUPPLIER

    ntn: Annotated[str | None, Field(pattern=NTN_PATTERN, examples=["1234567-8"])] = None
    strn: Annotated[str | None, Field(pattern=STRN_PATTERN)] = None
    cnic: Annotated[str | None, Field(pattern=CNIC_PATTERN, examples=["35202-1234567-8"])] = None
    is_filer: bool | None = None
    withholding_exempt: bool = False
    withholding_certificate_ref: Annotated[str | None, Field(max_length=80)] = None

    payment_terms_days: Annotated[int, Field(ge=0, le=365)] = 30
    credit_limit: Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=4)] = None
    currency_code: Annotated[str, Field(min_length=3, max_length=3)] = "PKR"

    phone: Annotated[str | None, Field(max_length=32)] = None
    email: Annotated[str | None, Field(max_length=160)] = None
    website: Annotated[str | None, Field(max_length=200)] = None
    address: AddressIn | None = None
    notes: Annotated[str | None, Field(max_length=2000)] = None

    contacts: list[VendorContactIn] = Field(default_factory=list)
    material_ids: list[UUID] = Field(default_factory=list)

    @field_validator("legal_name", "trade_name")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value else value

    @model_validator(mode="after")
    def _identifiable_for_tax(self) -> VendorCreate:
        """A supplier needs some tax identity, or AP cannot withhold correctly.

        NTN for a registered business, CNIC for a sole proprietor. Which one is
        a business fact, so the rule is "at least one", not "NTN always".
        """
        if not self.ntn and not self.cnic:
            raise ValueError(
                "Provide an NTN (registered business) or a CNIC (sole proprietor); "
                "withholding tax cannot be computed without one"
            )
        if sum(contact.is_primary for contact in self.contacts) > 1:
            raise ValueError("Only one contact can be the primary contact")
        return self


class VendorUpdate(ApiModel):
    """Partial update. `status` is deliberately absent — status changes go
    through the approve / suspend endpoints so they carry a reason and an audit
    action of their own."""

    legal_name: Annotated[str | None, Field(min_length=2, max_length=200)] = None
    trade_name: Annotated[str | None, Field(max_length=200)] = None
    vendor_type: VendorType | None = None
    ntn: Annotated[str | None, Field(pattern=NTN_PATTERN)] = None
    strn: Annotated[str | None, Field(pattern=STRN_PATTERN)] = None
    cnic: Annotated[str | None, Field(pattern=CNIC_PATTERN)] = None
    is_filer: bool | None = None
    withholding_exempt: bool | None = None
    withholding_certificate_ref: Annotated[str | None, Field(max_length=80)] = None
    payment_terms_days: Annotated[int | None, Field(ge=0, le=365)] = None
    credit_limit: Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=4)] = None
    phone: Annotated[str | None, Field(max_length=32)] = None
    email: Annotated[str | None, Field(max_length=160)] = None
    website: Annotated[str | None, Field(max_length=200)] = None
    address: AddressIn | None = None
    notes: Annotated[str | None, Field(max_length=2000)] = None


class VendorRead(ApiModel):
    id: UUID
    code: str
    legal_name: str
    trade_name: str | None
    display_name: str
    vendor_type: str
    status: str
    is_tradeable: bool

    ntn: str | None
    strn: str | None
    cnic: str | None
    is_filer: bool | None
    withholding_exempt: bool

    payment_terms_days: int
    credit_limit: Decimal | None
    currency_code: str

    phone: str | None
    email: str | None
    website: str | None
    address: dict[str, Any] | None
    notes: str | None

    approved_at: datetime | None
    suspended_at: datetime | None
    suspension_reason: str | None

    version: int
    created_at: datetime
    updated_at: datetime


class VendorDetail(VendorRead):
    contacts: list[VendorContactRead] = Field(default_factory=list)
    material_count: int = 0
    # Present only for callers holding vendors.manage_bank_details; everyone
    # else sees the masked summary below.
    bank_accounts: list[VendorBankAccountRead] | None = None
    primary_bank_masked: str | None = None


class VendorListItem(ApiModel):
    """The list view. No tax or bank identifiers: a vendor grid is read by many
    more people than should see those."""

    id: UUID
    code: str
    display_name: str
    vendor_type: str
    status: str
    payment_terms_days: int
    phone: str | None
    email: str | None
    city: str | None = None
    material_count: int = 0
    updated_at: datetime


# -----------------------------------------------------------------------------
# Status transitions
# -----------------------------------------------------------------------------


class VendorApprove(ApiModel):
    note: Annotated[str | None, Field(max_length=500)] = None


class VendorSuspend(ApiModel):
    # Required: a suspended vendor with no stated reason is an argument waiting
    # to happen, and the CHECK constraint refuses it anyway.
    reason: Annotated[str, Field(min_length=5, max_length=500)]
    blacklist: bool = False


class VendorStatusRead(ApiModel):
    id: UUID
    code: str
    status: VendorStatus
    suspension_reason: str | None


# -----------------------------------------------------------------------------
# Bank accounts
# -----------------------------------------------------------------------------


class VendorBankAccountIn(ApiModel):
    account_title: Annotated[str, Field(min_length=2, max_length=200)]
    account_no: Annotated[str | None, Field(max_length=40)] = None
    iban: Annotated[str | None, Field(min_length=15, max_length=34)] = None
    bank_name: Annotated[str, Field(min_length=2, max_length=160)]
    branch_name: Annotated[str | None, Field(max_length=160)] = None
    branch_code: Annotated[str | None, Field(max_length=20)] = None
    currency_code: Annotated[str, Field(min_length=3, max_length=3)] = "PKR"
    is_primary: bool = False
    verification_note: Annotated[str | None, Field(max_length=300)] = None

    @model_validator(mode="after")
    def _has_an_account_identifier(self) -> VendorBankAccountIn:
        if not self.account_no and not self.iban:
            raise ValueError("Provide an account number or an IBAN")
        return self


class VendorBankAccountRead(ApiModel):
    id: UUID
    account_title: str
    account_no: str | None
    iban: str | None
    bank_name: str
    branch_name: str | None
    branch_code: str | None
    currency_code: str
    is_primary: bool
    is_verified: bool
    verified_at: datetime | None
    masked_account: str


def _rebuild() -> None:
    VendorDetail.model_rebuild()


_rebuild()

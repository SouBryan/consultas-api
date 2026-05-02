import re
from ipaddress import IPv4Address
from typing import Literal

from pydantic import BaseModel, field_validator


ConsultationType = Literal[
    "cpf",
    "nome",
    "telefone",
    "cep",
    "email",
    "ip",
    "titulo",
    "pix",
]


CPFBase = Literal[
    "completo",
    "fotos",
    "vizinhos",
    "empregos",
    "vacinas",
    "beneficios",
    "internet",
    "obito",
    "compras",
]

NomeBase = Literal["nome", "nome_mae"]
TelefoneBase = Literal["telefone"]
EmailBase = Literal["email"]

CPF_BASE_BUTTON_MAP: dict[CPFBase, str] = {
    "completo": "CPF | COMPLETO",
    "fotos": "FOTOS",
    "vizinhos": "VIZINHOS",
    "empregos": "EMPREGOS",
    "vacinas": "VACINAS",
    "beneficios": "BENEFÍCIOS",
    "internet": "INTERNET",
    "obito": "ÓBITO",
    "compras": "COMPRAS",
}

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def _normalize_digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit())


def _validate_full_name(value: str, *, allow_single_word: bool = False) -> str:
    normalized = _normalize_whitespace(value)
    if len(normalized) < 3:
        raise ValueError("Nome deve conter pelo menos 3 caracteres.")
    if not allow_single_word and len(normalized.split()) < 2:
        raise ValueError("Nome deve conter pelo menos nome e sobrenome.")
    return normalized


class ConsultaCPFRequest(BaseModel):
    cpf: str
    base: CPFBase = "completo"

    @field_validator("cpf")
    @classmethod
    def validate_cpf(cls, value: str) -> str:
        digits_only = _normalize_digits(value)
        if len(digits_only) != 11:
            raise ValueError("CPF deve conter 11 dígitos.")
        return digits_only

    @property
    def query_input(self) -> str:
        return self.cpf


class ConsultaNomeRequest(BaseModel):
    nome: str
    base: NomeBase = "nome"

    @field_validator("nome")
    @classmethod
    def validate_nome(cls, value: str) -> str:
        return _validate_full_name(value)

    @property
    def query_input(self) -> str:
        return self.nome


class ConsultaTelefoneRequest(BaseModel):
    telefone: str
    base: TelefoneBase = "telefone"

    @field_validator("telefone")
    @classmethod
    def validate_telefone(cls, value: str) -> str:
        digits_only = _normalize_digits(value)
        if len(digits_only) not in {10, 11}:
            raise ValueError("Telefone deve conter 10 ou 11 dígitos.")
        return digits_only

    @property
    def query_input(self) -> str:
        return self.telefone


class ConsultaCEPRequest(BaseModel):
    cep: str

    @field_validator("cep")
    @classmethod
    def validate_cep(cls, value: str) -> str:
        digits_only = _normalize_digits(value)
        if len(digits_only) != 8:
            raise ValueError("CEP deve conter 8 dígitos.")
        return digits_only

    @property
    def query_input(self) -> str:
        return self.cep


class ConsultaEmailRequest(BaseModel):
    email: str
    base: EmailBase = "email"

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("Email inválido.")
        return normalized

    @property
    def query_input(self) -> str:
        return self.email


class ConsultaIPRequest(BaseModel):
    ip: str

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value: str) -> str:
        normalized = value.strip()
        try:
            return str(IPv4Address(normalized))
        except ValueError as exc:
            raise ValueError("IP deve ser um IPv4 válido.") from exc

    @property
    def query_input(self) -> str:
        return self.ip


class ConsultaTituloRequest(BaseModel):
    titulo: str

    @field_validator("titulo")
    @classmethod
    def validate_titulo(cls, value: str) -> str:
        digits_only = _normalize_digits(value)
        if len(digits_only) != 12:
            raise ValueError("Título deve conter 12 dígitos.")
        return digits_only

    @property
    def query_input(self) -> str:
        return self.titulo


class ConsultaPIXRequest(BaseModel):
    nome: str
    meio_cpf: str

    @field_validator("nome")
    @classmethod
    def validate_nome(cls, value: str) -> str:
        return _validate_full_name(value)

    @field_validator("meio_cpf")
    @classmethod
    def validate_meio_cpf(cls, value: str) -> str:
        digits_only = _normalize_digits(value)
        if len(digits_only) != 6:
            raise ValueError("Meio CPF deve conter 6 dígitos.")
        return digits_only

    @property
    def query_input(self) -> str:
        return f"{self.nome}|{self.meio_cpf}"


class ConsultaGenericaRequest(BaseModel):
    tipo: ConsultationType
    input: str
    base: str | None = None

    @field_validator("input")
    @classmethod
    def validate_input(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Input não pode ser vazio.")
        return normalized

    @field_validator("base")
    @classmethod
    def validate_base(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
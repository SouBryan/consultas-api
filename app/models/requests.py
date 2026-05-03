import re
from ipaddress import IPv4Address
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ConsultationType = Literal[
    "cpf",
    "nome",
    "telefone",
    "cep",
    "email",
    "cnpj",
    "bin",
    "endereco",
    "mae",
    "foto",
    "rg",
    "pai",
    "placa",
    "proprietario",
    "cns",
    "chave",
    "vizinhos",
    "parentes",
    "pep",
    "frota",
    "processo_numero",
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
PLATE_PATTERN = re.compile(r"^[A-Z]{3}[0-9][A-Z0-9][0-9]{2}$")


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def _normalize_digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit())


def _normalize_alphanumeric(value: str) -> str:
    return "".join(char for char in value if char.isalnum()).upper()


def _validate_full_name(value: str, *, allow_single_word: bool = False) -> str:
    normalized = _normalize_whitespace(value)
    if len(normalized) < 3:
        raise ValueError("Nome deve conter pelo menos 3 caracteres.")
    if not allow_single_word and len(normalized.split()) < 2:
        raise ValueError("Nome deve conter pelo menos nome e sobrenome.")
    return normalized


def _validate_plate(value: str) -> str:
    normalized = _normalize_alphanumeric(value)
    if not PLATE_PATTERN.fullmatch(normalized):
        raise ValueError("Placa deve estar no formato ABC1234 ou ABC1D23.")
    return normalized


def _validate_process_number(value: str) -> str:
    normalized = re.sub(r"\s+", "", value)
    if len(normalized) < 5:
        raise ValueError("Número do processo deve conter pelo menos 5 caracteres.")
    return normalized


def _validate_rg(value: str) -> str:
    normalized = _normalize_alphanumeric(value)
    if len(normalized) < 5:
        raise ValueError("RG deve conter pelo menos 5 caracteres alfanuméricos.")
    return normalized


class ConsultaCPFRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "cpf": "12974572936",
                "base": "completo",
            }
        }
    )

    cpf: str = Field(description="CPF com 11 dígitos, com ou sem pontuação.")
    base: CPFBase = Field(default="completo", description="Sub-base gratuita da consulta CPF.")

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
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "nome": "João da Silva Santos",
                "base": "nome",
            }
        }
    )

    nome: str = Field(description="Nome completo da pessoa consultada.")
    base: NomeBase = Field(default="nome", description="Sub-base da consulta por nome.")

    @field_validator("nome")
    @classmethod
    def validate_nome(cls, value: str) -> str:
        return _validate_full_name(value)

    @property
    def query_input(self) -> str:
        return self.nome


class ConsultaTelefoneRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "telefone": "44988030666",
                "base": "telefone",
            }
        }
    )

    telefone: str = Field(description="Telefone com DDD e 10 ou 11 dígitos.")
    base: TelefoneBase = Field(default="telefone", description="Base disponível para consulta de telefone.")

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
    model_config = ConfigDict(json_schema_extra={"example": {"cep": "87020025"}})

    cep: str = Field(description="CEP com 8 dígitos.")

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
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "joao@gmail.com",
                "base": "email",
            }
        }
    )

    email: str = Field(description="Endereço de e-mail válido.")
    base: EmailBase = Field(default="email", description="Base disponível para consulta de e-mail.")

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


class ConsultaCNPJRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"cnpj": "33000167000101"}})

    cnpj: str = Field(description="CNPJ com 14 dígitos.")

    @field_validator("cnpj")
    @classmethod
    def validate_cnpj(cls, value: str) -> str:
        digits_only = _normalize_digits(value)
        if len(digits_only) != 14:
            raise ValueError("CNPJ deve conter 14 dígitos.")
        return digits_only

    @property
    def query_input(self) -> str:
        return self.cnpj


class ConsultaBINRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"bin": "516230"}})

    bin: str = Field(description="BIN com 6 a 8 dígitos.")

    @field_validator("bin")
    @classmethod
    def validate_bin(cls, value: str) -> str:
        digits_only = _normalize_digits(value)
        if len(digits_only) < 6 or len(digits_only) > 8:
            raise ValueError("BIN deve conter entre 6 e 8 dígitos.")
        return digits_only

    @property
    def query_input(self) -> str:
        return self.bin


class ConsultaEnderecoRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"cpf": "07068093868"}})

    cpf: str = Field(description="CPF com 11 dígitos usado na consulta de endereço.")

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


class ConsultaMaeRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"nome": "Maria Alves"}})

    nome: str = Field(description="Nome completo da mãe a ser consultado.")

    @field_validator("nome")
    @classmethod
    def validate_nome(cls, value: str) -> str:
        return _validate_full_name(value)

    @property
    def query_input(self) -> str:
        return self.nome


class ConsultaFotoRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"cpf": "07068093868"}})

    cpf: str = Field(description="CPF com 11 dígitos usado na consulta de foto.")

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


class ConsultaRGRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"rg": "234730742"}})

    rg: str = Field(description="RG alfanumérico a ser consultado.")

    @field_validator("rg")
    @classmethod
    def validate_rg(cls, value: str) -> str:
        return _validate_rg(value)

    @property
    def query_input(self) -> str:
        return self.rg


class ConsultaPaiRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"nome": "Jose Silva"}})

    nome: str = Field(description="Nome completo do pai a ser consultado.")

    @field_validator("nome")
    @classmethod
    def validate_nome(cls, value: str) -> str:
        return _validate_full_name(value)

    @property
    def query_input(self) -> str:
        return self.nome


class ConsultaPlacaRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"placa": "ABC1D23"}})

    placa: str = Field(description="Placa no padrão antigo ou Mercosul.")

    @field_validator("placa")
    @classmethod
    def validate_placa(cls, value: str) -> str:
        return _validate_plate(value)

    @property
    def query_input(self) -> str:
        return self.placa


class ConsultaProprietarioRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"placa": "ABC1D23"}})

    placa: str = Field(description="Placa usada na consulta de proprietário.")

    @field_validator("placa")
    @classmethod
    def validate_placa(cls, value: str) -> str:
        return _validate_plate(value)

    @property
    def query_input(self) -> str:
        return self.placa


class ConsultaCNSRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"cns": "705005484822659"}})

    cns: str = Field(description="Número CNS com 15 dígitos.")

    @field_validator("cns")
    @classmethod
    def validate_cns(cls, value: str) -> str:
        digits_only = _normalize_digits(value)
        if len(digits_only) != 15:
            raise ValueError("CNS deve conter 15 dígitos.")
        return digits_only

    @property
    def query_input(self) -> str:
        return self.cns


class ConsultaChaveRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"cpf": "07068093868"}})

    cpf: str = Field(description="CPF com 11 dígitos usado na consulta de chave PIX.")

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


class ConsultaVizinhosRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"cpf": "07068093868"}})

    cpf: str = Field(description="CPF com 11 dígitos usado na consulta de vizinhos.")

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


class ConsultaParentesRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"cpf": "07068093868"}})

    cpf: str = Field(description="CPF com 11 dígitos usado na consulta de parentes.")

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


class ConsultaPEPRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"cpf": "07068093868"}})

    cpf: str = Field(description="CPF com 11 dígitos usado na consulta de PEP.")

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


class ConsultaFrotaRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"cnpj": "33000167000101"}})

    cnpj: str = Field(description="CNPJ com 14 dígitos usado na consulta de frota.")

    @field_validator("cnpj")
    @classmethod
    def validate_cnpj(cls, value: str) -> str:
        digits_only = _normalize_digits(value)
        if len(digits_only) != 14:
            raise ValueError("CNPJ deve conter 14 dígitos.")
        return digits_only

    @property
    def query_input(self) -> str:
        return self.cnpj


class ConsultaProcessoNumeroRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"numero": "1234567"}})

    numero: str = Field(description="Número do processo a ser consultado.")

    @field_validator("numero")
    @classmethod
    def validate_numero(cls, value: str) -> str:
        return _validate_process_number(value)

    @property
    def query_input(self) -> str:
        return self.numero


class ConsultaIPRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"ip": "8.8.8.8"}})

    ip: str = Field(description="Endereço IPv4 válido.")

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
    model_config = ConfigDict(json_schema_extra={"example": {"titulo": "018921371805"}})

    titulo: str = Field(description="Título de eleitor com 12 dígitos.")

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
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "nome": "douglas da costa silva",
                "meio_cpf": "226471",
            }
        }
    )

    nome: str = Field(description="Nome completo para consulta PIX.")
    meio_cpf: str = Field(description="Seis dígitos centrais do CPF.")

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
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tipo": "cpf",
                "input": "12974572936",
                "base": "completo",
            }
        }
    )

    tipo: ConsultationType = Field(description="Tipo de consulta desejado.")
    input: str = Field(description="Input bruto compatível com o tipo informado.")
    base: str | None = Field(default=None, description="Base opcional, exigida por alguns tipos.")

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
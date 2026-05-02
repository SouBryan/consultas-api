from typing import Literal

from pydantic import BaseModel, field_validator


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


class ConsultaCPFRequest(BaseModel):
    cpf: str
    base: CPFBase = "completo"

    @field_validator("cpf")
    @classmethod
    def validate_cpf(cls, value: str) -> str:
        digits_only = "".join(char for char in value if char.isdigit())
        if len(digits_only) != 11:
            raise ValueError("CPF deve conter 11 dígitos.")
        return digits_only

    @property
    def base_button_text(self) -> str:
        return CPF_BASE_BUTTON_MAP[self.base]
from fastapi import FastAPI
from datetime import datetime, date
import pandas as pd
import requests


app = FastAPI()


@app.get("/vagas/{estacionamento_id}")
def get_vagas(estacionamento_id: int):
    estacionamento = pd.read_json("http://localhost:8000/estacionamento").query(
        "id == @estacionamento_id"
    )
    catraca = pd.read_json("http://localhost:8000/catraca").query(
        "estacionamento_id == @estacionamento_id"
    )
    registro = (
        pd.read_json(f"http://localhost:8000/registro/{estacionamento_id}?from_timestamp={date.today().isoformat()}&to_timestamp={datetime.now().isoformat()}&ascending=true")
        .merge(catraca, left_on="catraca_id", right_on="id", how="left")
    )

    registro["_rn"] = registro.groupby(["aluno_id", "tipo_catraca"]).cumcount()
    entrada = registro.query("tipo_catraca == 'entrada'")
    saida = registro.query("tipo_catraca == 'saida'")

    fact = entrada.merge(
        saida, on=["aluno_id", "_rn"], suffixes=("_entrada", "_saida"), how="left"
    )

    vaga_ocupada = fact.query("timestamp_saida.isnull()").shape[0]
    capacidade = estacionamento["capacidade"].values[0]
    return int(capacidade - vaga_ocupada)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        port=8001,
        reload=True,
        reload_dirs=["src"],
        reload_delay=2.0,
    )

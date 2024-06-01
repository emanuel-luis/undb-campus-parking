from datetime import datetime, date, timedelta
import pandas as pd

from fastapi import FastAPI
from pathlib import Path

from random import choice
from faker import Faker
from datetime import datetime, timedelta

curdir = Path(__file__).parent
app = FastAPI()


@app.get("/")
def root():
    return [
        {
            "path": "/aluno/",
            "description": "Get all students",
        },
        {
            "path": "/estacionamento/",
            "description": "Get all parking lots",
        },
        {
            "path": "/catraca/",
            "description": "Get all turnstiles",
        },
        {
            "path": "/entry/",
            "description": "Get all entries",
        },
        {
            "path": "/entry/{estacionamento_id}",
            "description": "Get all entries by parking lot",
        },
    ]


@app.get("/generate")
def generate_data(size: int = 1_000):
    fake = Faker()
    fake.seed(42)

    # Generate data for Aluno
    data = []
    for _id in range(10_000):
        data.append(
            {
                "id": _id + 1,
                "name": fake.name(),
                "email": fake.email(),
            }
        )

    pd.DataFrame(data).to_parquet("data/aluno.parquet", compression="snappy")

    # Generate data for Estacionamento
    data = [
        {"id": 1, "name": "Estacionamento 1", "capacidade": 200},
        {"id": 2, "name": "Estacionamento 2", "capacidade": 150},
    ]

    pd.DataFrame(data).to_parquet("data/estacionamento.parquet", compression="snappy")

    # Generate data for Catraca
    data = [
        {
            "id": 1,
            "name": "Catraca 1",
            "estacionamento_id": 1,
            "tipo_catraca": "entrada",
        },
        {"id": 2, "name": "Catraca 2", "estacionamento_id": 1, "tipo_catraca": "saida"},
        {
            "id": 3,
            "name": "Catraca 3",
            "estacionamento_id": 2,
            "tipo_catraca": "entrada",
        },
        {"id": 4, "name": "Catraca 4", "estacionamento_id": 2, "tipo_catraca": "saida"},
    ]

    pd.DataFrame(data).to_parquet("data/catraca.parquet", compression="snappy")

    # Generate data for Registro
    data = []

    registered_student = {}

    init_datetime = datetime.fromisoformat(date.today().isoformat())
    for _id in range(size):
        aluno_id = fake.random_int(min=1, max=10_000)
        catraca_id = choice([1, 3])
        ts_entrada = init_datetime
        ts_saida = init_datetime + timedelta(seconds=fake.random_int(min=60, max=60*60))

        if registered_student.get(aluno_id, ts_saida) < ts_entrada:
            ts_entrada = registered_student[aluno_id] + timedelta(
                seconds=fake.random_int(min=10 * 60, max=60 * 60)
            )
            ts_saida = ts_entrada + timedelta(
                seconds=fake.random_int(min=60, max=60 * 60)
            )

        registered_student[aluno_id] = ts_saida
        entrada = {
            "aluno_id": aluno_id,
            "catraca_id": catraca_id,
            "timestamp": ts_entrada,
        }

        saida = {
            "aluno_id": aluno_id,
            "catraca_id": catraca_id + 1,
            "timestamp": ts_saida,
        }

        data.append(entrada)
        data.append(saida)
        init_datetime += timedelta(seconds=fake.random_int(min=0, max=60*30))

    pd.DataFrame(data).to_parquet("data/registro.parquet", compression="snappy")

    return {"message": "Data generated successfully"}


@app.get("/aluno")
def get_aluno():
    return pd.read_parquet("./data/aluno.parquet").to_dict("records")


@app.get("/estacionamento")
def get_estacionamento():
    return pd.read_parquet("./data/estacionamento.parquet").to_dict("records")


@app.get("/catraca")
def get_catraca():
    return pd.read_parquet("./data/catraca.parquet").to_dict("records")


@app.get("/registro")
def get_entry(
    from_timestamp: datetime = date.today(),
    to_timestamp: datetime = datetime.now(),
):
    return get_entry_filtered(None, from_timestamp, to_timestamp)


@app.get("/registro/{estacionamento_id}")
def get_entry_filtered(
    estacionamento_id: int,
    from_timestamp: datetime = date.today(),
    to_timestamp: datetime = datetime.now(),
    ascending: bool = True,
):
    catraca_df = pd.read_parquet("./data/catraca.parquet")
    if estacionamento_id:
        catraca_df.query("estacionamento_id == @estacionamento_id", inplace=True)

    return (
        pd.read_parquet("./data/registro.parquet")
        .query("catraca_id in @catraca_df.id")
        .query("timestamp <= @to_timestamp")
        .query("timestamp >= @from_timestamp")
        .sort_values("timestamp", ascending=ascending)
        .to_dict("records")
    )


WEBSERVER_PORT = 8000

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        'main:app',
        port=WEBSERVER_PORT,
        reload=True,
        reload_dirs=["src"],
        reload_delay=2.0,
    )

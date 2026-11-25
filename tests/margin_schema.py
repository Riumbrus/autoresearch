"""Пример схемы для контрольной выгрузки маржи."""
import sys

import pandas as pd
def validate(frame):
    try:
        import pandera.pandas as pa
    except ModuleNotFoundError as exc:
        if exc.name != "pandera":
            raise
        columns = ["period", "sku", "revenue", "profit"]
        data = frame.loc[:, columns]
        issues = []
        if data.isna().any().any():
            issues.append("пропущенные значения")
        if not pd.api.types.is_integer_dtype(data.period):
            issues.append("период должен быть целым")
        if not data.sku.map(lambda sku: isinstance(sku, str)).all():
            issues.append("SKU должен быть строкой")
        if data.duplicated(["period", "sku"]).any():
            issues.append("повтор ключа период/SKU")
        revenue = pd.to_numeric(data.revenue, errors="coerce")
        if not revenue.gt(0).all() or pd.to_numeric(data.profit, errors="coerce").isna().any():
            issues.append("проверьте выручку и прибыль")
        if issues:
            raise ValueError(", ".join(issues))
        return
    schema = pa.DataFrameSchema(
    {
        "period": pa.Column(int),
        "sku": pa.Column(str),
        "revenue": pa.Column(float, pa.Check.gt(0), coerce=True),
        "profit": pa.Column(float, coerce=True),
    },
    unique=["period", "sku"],
    )
    schema.validate(frame, lazy=True)

if __name__ == "__main__":
    validate(pd.read_csv(sys.argv[1], dtype={"sku": str}))

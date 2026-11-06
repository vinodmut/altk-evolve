"""Regenerate shopkit/models.py and the Python client from schema/models.yaml."""

import schema

models = schema.load()
(schema.ROOT / "shopkit" / "models.py").write_text(schema.render_models(models))
(schema.ROOT / "clients" / "python" / "shopkit_client.py").write_text(schema.render_client(models))
print(f"generated shopkit/models.py and clients/python/shopkit_client.py (schema {schema.digest()})")

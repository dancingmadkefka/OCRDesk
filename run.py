#!/usr/bin/env python3
"""Start the OCR benchmark server."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("backend.server:app", host="127.0.0.1", port=8877, reload=False)

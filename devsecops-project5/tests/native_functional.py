"""Run unchanged V1 functional assertions against the isolated data namespace."""
import os
import functional

functional.BASE = os.environ.get("PROJECT5_BASE", "http://127.0.0.1:8080")
original_k = functional.k


def k(*args):
    args = list(args)
    if any(value in args for value in ("pvc", "app=postgres", "deployment/postgres")):
        args[args.index("project5")] = "project5-data"
    return original_k(*args)


functional.k = k
if __name__ == "__main__":
    functional.main()

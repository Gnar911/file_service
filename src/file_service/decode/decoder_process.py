




def _run_decode_job(status_queue: Any, decode_jobs: Iterable[tuple[str, str]]) -> None:
    try:
        decode_process(status_queue, list(decode_jobs))
    except Exception as exc:
        status_queue.put({"type": "DECODE_ERROR", "error": str(exc)})

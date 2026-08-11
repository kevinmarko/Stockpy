import asyncio
import os
import time

def _read_lines(path, offset):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        return f.readlines(), f.tell()

async def baseline_poll(log_path, offset, iterations):
    start = time.perf_counter()
    for _ in range(iterations):
        lines, new_offset = await asyncio.to_thread(_read_lines, log_path, offset)
    end = time.perf_counter()
    return end - start

async def optimized_poll(log_path, offset, iterations):
    start = time.perf_counter()
    for _ in range(iterations):
        if os.stat(log_path).st_size > offset:
            lines, new_offset = await asyncio.to_thread(_read_lines, log_path, offset)
    end = time.perf_counter()
    return end - start

async def main():
    log_path = "test_benchmark.log"
    with open(log_path, "w") as f:
        f.write("Some initial log lines\n" * 100)

    offset = os.stat(log_path).st_size
    iterations = 1000

    print("Running baseline...")
    baseline_time = await baseline_poll(log_path, offset, iterations)

    print("Running optimized...")
    optimized_time = await optimized_poll(log_path, offset, iterations)

    print(f"Iterations: {iterations}")
    print(f"Baseline Time: {baseline_time:.4f}s")
    print(f"Optimized Time: {optimized_time:.4f}s")
    if baseline_time > 0:
        improvement = (baseline_time - optimized_time) / baseline_time * 100
        print(f"Speedup: {baseline_time/optimized_time:.2f}x")
        print(f"Time reduction: {improvement:.2f}%")

    os.remove(log_path)

if __name__ == "__main__":
    asyncio.run(main())

"""Independent seeded streams for MaxSim synthetic inputs."""

from array import array
from bisect import bisect_right
from heapq import nsmallest
import math
import random

from config import uint

ALGORITHMS = {
    "layout": "mt19937-shuffle-ranked-id-array/1",
    "candidates": "uniform-sample-or-zipf-cdf-rejection-race-fallback/1",
    "arrivals": "mt19937-expovariate-cumulative-ceil-ns/1",
}


def stream(domain, seed):
    return random.Random(f"maxsim/1/{domain}/{seed}")


class Candidates:
    def __init__(self, config):
        work, seeds = config["workload"], config["generation"]
        self.size = work["corpus_documents"]
        self.count = work["candidate_count"]
        self.alpha = work["zipf_alpha"]
        self.rng = stream("candidates", seeds["candidate_seed"])
        self.rank_to_id = range(self.size)
        if work["randomize_rank_to_document"]:
            self.rank_to_id = array("Q", self.rank_to_id)
            stream("layout", seeds["layout_seed"]).shuffle(self.rank_to_id)
        self.cdf = array("d")
        if self.alpha:
            cumulative = 0.0
            for rank in range(1, self.size + 1):
                cumulative += rank ** -self.alpha
                self.cdf.append(cumulative)

    def draw(self):
        if not self.cdf:
            ranks = self.rng.sample(range(self.size), self.count)
        else:
            ranks = self._zipf()
        return [self.rank_to_id[rank] for rank in ranks]

    def _zipf(self):
        ranks, seen = [], set()
        rejections = 0
        while len(ranks) < self.count:
            draw = min(self.rng.random() * self.cdf[-1], math.nextafter(self.cdf[-1], 0.0))
            rank = bisect_right(self.cdf, draw)
            if rank not in seen:
                seen.add(rank)
                ranks.append(rank)
                rejections = 0
            else:
                rejections += 1
                if rejections == 64:
                    # Ordered exponential races give the same sequential weighted
                    # law among the remaining ranks, including near full coverage.
                    remaining = ((-math.log1p(-self.rng.random()) * (rank + 1) ** self.alpha, rank)
                                 for rank in range(self.size) if rank not in seen)
                    ranks.extend(rank for _, rank in nsmallest(self.count - len(ranks), remaining))
                    break
        return ranks


def arrivals(config):
    load, generation = config["load"], config["generation"]
    rng = stream("arrivals", generation["arrival_seed"])
    seconds = 0.0
    for query in range(generation["queries"]):
        seconds += rng.expovariate(load["open_loop_qps"])
        time_ns = seconds * 1_000_000_000
        if not math.isfinite(time_ns):
            raise ValueError("open-loop offered_ns overflow")
        yield {"query": query, "offered_ns": uint(math.ceil(time_ns), "offered_ns")}

"""Exact ACG/CCG fixtures, no real-recording or model writes."""
from pathlib import Path
import sys,unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'mockups/curation-review/backend'))
from review_correlograms import correlogram, EDGES, CorrelogramCache

class CorrelogramTests(unittest.TestCase):
    def expected(self,a,b,same=False):
        counts=np.zeros(100,dtype=int)
        for i,x in enumerate(a):
            for j,y in enumerate(b):
                if same and i==j:continue
                delta=y-x
                if EDGES[0]<=delta<EDGES[-1]:counts[np.searchsorted(EDGES,delta,side='right')-1]+=1
        return counts
    def test_all_pairs_including_nonadjacent_and_coincident_distinct_spikes(self):
        a=[0,0,.01,.02]
        v=correlogram(a)
        np.testing.assert_array_equal(v['counts'],self.expected(a,a,True))
        self.assertEqual(sum(v['counts']),12)
        self.assertEqual(v['counts'][50],2)
        self.assertTrue(v['self_pairs_excluded'])
    def test_signed_lags_and_half_open_exact_boundaries_in_both_size_orders(self):
        a=[0, .000123]
        b=[-.05,-.049,-.001,0,.001,.049,.05,.050001]
        for x,y in [(a,b),(b,a)]:
            np.testing.assert_array_equal(correlogram(x,y)['counts'],self.expected(x,y))
    def test_unsorted_members_and_chunk_boundary_equal_brute_force(self):
        a=np.linspace(0,8,2055)[::-1];b=np.array([.0333,2.00012,4.12345,8.001])
        np.testing.assert_array_equal(correlogram(a,b)['counts'],self.expected(a,b))
    def test_empty_singleton_and_invalid_data(self):
        self.assertEqual(sum(correlogram([])['counts']),0)
        self.assertEqual(sum(correlogram([1])['counts']),0)
        for x in [[np.nan],[[1,2]],[np.inf]]:
            with self.assertRaises(ValueError):correlogram(x)
        with self.assertRaises(ValueError):correlogram([0],[1],self_pairs=True)
    def test_cache_preserves_order_and_does_not_confuse_acg_with_ccg(self):
        cache=CorrelogramCache();a=[0,.001,.004];b=[.002]
        for x,y in [(a,b),(b,a)]:np.testing.assert_array_equal(cache.get(x,y)['counts'],self.expected(x,y))
        self.assertEqual(sum(cache.get(a)['counts']),6)
        self.assertEqual(sum(cache.get(a,a)['counts']),9)
        self.assertEqual(len(cache.values),4)

if __name__=='__main__':unittest.main()

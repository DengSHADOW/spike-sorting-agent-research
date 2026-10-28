"""All-member display regressions: no model, database or real-recording writes."""
from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'mockups/curation-review/backend'))
import build_pair_waveforms as renderer

class FullMemberRasterTests(unittest.TestCase):
    def test_every_original_waveform_reaches_the_renderer_including_last_outlier(self):
        from matplotlib.collections import LineCollection
        from PIL import Image
        waves=np.zeros((4101,3));waves[:,1]=np.arange(4101)/100;waves[-1]=[-90,230,-170]
        original=waves.copy();seen=[]
        def collection(segments,*args,**kwargs):
            seen.append(segments.copy());return LineCollection(segments,*args,**kwargs)
        with patch('matplotlib.collections.LineCollection',side_effect=collection):
            png,meta=renderer.rasterize(waves,30000)
        segments=np.concatenate(seen)
        np.testing.assert_array_equal(segments[:,:,1],original)
        np.testing.assert_allclose(segments[0,:,0],np.arange(3)/30000*1000)
        np.testing.assert_array_equal(waves,original)
        self.assertEqual(meta['n_waveforms'],4101);self.assertTrue(meta['all_members'])
        self.assertFalse(meta['mean']);self.assertFalse(meta['sd_band'])
        self.assertEqual(meta['raw_low'],-170);self.assertEqual(meta['raw_high'],230)
        image=Image.open(BytesIO(png));self.assertEqual(image.size,(renderer.WIDTH,renderer.HEIGHT))
        alpha=np.asarray(image.getchannel('A'));self.assertTrue(np.any(alpha>0));self.assertTrue(np.any(alpha==0))

    def test_invalid_arrays_are_rejected_instead_of_silently_sampling_or_imputing(self):
        for waves in (np.empty((0,3)),np.zeros((2,1)),np.array([[0,np.nan,1]])):
            with self.assertRaises(ValueError):renderer.rasterize(waves,30000)

if __name__=='__main__':unittest.main()

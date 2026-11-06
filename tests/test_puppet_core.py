import unittest
import numpy as np
from backend.puppet import compose_with_props,clean_plate

class PuppetCompositingTests(unittest.TestCase):
    def setUp(self):
        self.src=np.full((50,50,3),100,np.uint8)
        self.fg=np.zeros((50,50,4),np.float32)
        self.fg[20:30,20:30,:3]=200
        self.fg[20:30,20:30,3]=127.5
        self.P=np.zeros((50,50),np.uint8)
        self.support=np.zeros((50,50),np.float32)
    def test_new_geometry_alpha_is_applied_once(self):
        out,qa=compose_with_props(self.src,self.src,self.fg,self.P,change_support=self.support)
        np.testing.assert_array_equal(out[25,25],[150]*3)
        np.testing.assert_array_equal(out[0,0],self.src[0,0])
        self.assertEqual(qa['outside_registered_support_changed_pixels'],0)
    def test_half_prop_coverage_is_applied_once(self):
        self.P[20:30,20:30]=128
        src=self.src.copy();src[20:30,20:30]=150
        out,_=compose_with_props(src,self.src,self.fg,self.P,change_support=self.support)
        self.assertLessEqual(abs(int(out[25,25,0])-175),1)
    def test_opaque_prop_is_source_exact(self):
        self.P[20:30,20:30]=255
        src=self.src.copy();src[20:30,20:30]=231
        out,qa=compose_with_props(src,self.src,self.fg,self.P,change_support=self.support)
        np.testing.assert_array_equal(out[20:30,20:30],src[20:30,20:30])
        self.assertEqual(qa['opaque_props_changed_pixels'],0)
    def test_old_actor_removal_alpha_is_not_squared(self):
        clean=self.src.copy();clean[20:30,20:30]=150
        self.fg[:]=0;self.support[20:30,20:30]=.5
        out,_=compose_with_props(self.src,clean,self.fg,self.P,change_support=self.support)
        np.testing.assert_array_equal(out[25,25],[150]*3)
    def test_white_foreground_is_unmixed_once(self):
        src=self.src.copy();src[20:30,20:30]=193
        polygon=[[20,20],[29,20],[29,29],[20,29]]
        out,qa=compose_with_props(src,self.src,self.fg,self.P,effect_polygon=polygon,change_support=self.support)
        self.assertLessEqual(abs(int(out[25,25,0])-213),2)
        self.assertTrue(qa['white_effect_unmixed'])
    def test_registered_border_actor_does_not_restore_white_cloth(self):
        source=self.src.copy();source[10:40,40:]=230
        mask=np.zeros((50,50),np.uint8);mask[10:40,40:]=255
        actor={'id':'referee','edge_background_fill':'one_sided_source_scanline'}
        out,_=clean_plate(source,[mask],[actor])
        np.testing.assert_array_equal(out[25,48],[100]*3)
    def test_empty_removal_is_identity(self):
        out,a=clean_plate(self.src,[np.zeros((50,50),np.uint8)])
        np.testing.assert_array_equal(out,self.src);self.assertFalse(a.any())

if __name__=='__main__':unittest.main()

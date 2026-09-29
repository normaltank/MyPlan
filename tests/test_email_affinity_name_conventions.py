"""Field-found affinity gap: given-initial-prefixed surname local parts.

湖南师范大学 国际汉语文化学院 王婉婷's official profile states
"邮箱：wwang2026@hunnu.edu.cn" — the widespread Chinese university convention of
<given-initial><surname><digits>. The affinity model only produced <surname><given>
and <surname><given-initials> variants, so a genuine personal mailbox was rejected
and the scholar silently fell into the no-email sheet.

The extended variant stays name-derived (no keyword blacklist) and must keep
rejecting generic service mailboxes.
"""
from server.faculty_extractor import is_personal_email_for_scholar
from server.detail_mecc import is_personal_email_for_scholar as mecc_affinity


def test_given_initial_prefixed_surname_is_personal():
    assert is_personal_email_for_scholar("wwang2026@hunnu.edu.cn", "王婉婷") is True
    assert is_personal_email_for_scholar("lzhang@hunnu.edu.cn", "张磊") is True
    assert mecc_affinity("wwang2026@hunnu.edu.cn", "王婉婷") is True


def test_service_mailboxes_still_rejected():
    assert is_personal_email_for_scholar("office@gmail.com", "张三") is False
    assert is_personal_email_for_scholar("admin@163.com", "李四") is False
    assert is_personal_email_for_scholar("service@qq.com", "王五") is False
    assert is_personal_email_for_scholar("office@univ.edu.cn", "张三") is False


def test_existing_conventions_still_accepted():
    assert is_personal_email_for_scholar("zhangsan@gmail.com", "张三") is True
    assert is_personal_email_for_scholar("lsjwork@126.com", "刘圣洁") is True
    assert is_personal_email_for_scholar("wangwei_1985@163.com", "王伟") is True

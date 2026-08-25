from app.scl_test_details import parse_test_detail_page


def test_parses_public_test_detail_fields_and_container_guidance() -> None:
    html = """
    <table>
      <tr><th>검사명</th><td>Widal test</td></tr>
      <tr><th>SCL 검사코드</th><td>30130</td></tr>
      <tr><th>검체명</th><td><span>Serum</span></td></tr>
      <tr><th>보존방법</th><td>동결 or 냉장</td></tr>
      <tr><th>참고치</th><td><table><tr><td>Widal O</td><td>Negative</td></tr></table></td></tr>
      <tr><td><span>채취방법 및<br>주의사항</span></td><td>고 lipemic 혈청은 원심분리한다.</td></tr>
      <tr><th>임상적의의</th><td>장티푸스성 질환의 보조 진단 검사다.</td></tr>
    </table>
    <ul>
      <li><span class="key">첨가제</span><span class="value">응고촉진제, gel</span></li>
      <li><span class="key">주의사항/<br>참고</span><span class="value">채혈 후 충분히 혼합한다.</span></li>
    </ul>
    """

    detail = parse_test_detail_page(html, "https://www.scllab.co.kr/detail")

    assert detail.fields["검사명"] == "Widal test"
    assert detail.fields["채취방법 및 주의사항"] == "고 lipemic 혈청은 원심분리한다."
    assert "장티푸스성 질환" in detail.fields["임상적 의의"]
    assert detail.container["첨가제"] == "응고촉진제, gel"
    assert "채혈 후 충분히 혼합" in detail.full_text
    assert detail.content_hash

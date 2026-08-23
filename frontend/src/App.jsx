import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, ChevronDown, Pause, Play, Search, UserRound } from "lucide-react";
import { ChatWidget } from "./chat/ChatWidget.jsx";

const heroSlides = [
  { image: "/assets/scl/hero-lab.jpg", title: "혁신, 새로운 가능성에 대한 도전", body: "SCL은 끝없는 도전과 혁신으로\n건강한 내일을 만들어갑니다." },
  { image: "/assets/scl/hero-global.jpg", title: "세계를 선도하는 헬스케어 파트너", body: "국내 최초 검체 검사 전문기관 SCL\nGlobal Top-Tier를 향해 나아갑니다." },
  { image: "/assets/scl/hero-history.jpg", title: "40여 년간 이어온 기업의 가치", body: "모두가 건강하고 행복한 세상\nSCL이 함께합니다." },
];

const notices = [
  ["2026-08-06", "[공지]", "2026년 8월 연휴(광복절,대체휴일)에 따른 검사일정 변경 안내"],
  ["2026-08-06", "[SCL 2026-065호]", "Culture & I.D 검사 및 일부 검사정보 변경 안내"],
  ["2026-08-03", "[SCL 2026-064호]", "MAST 215종 검사의 신규검사 안내"],
];

const news = [
  { image: "/assets/scl/news-youth.jpg", title: "SCL그룹, 자립준비청년의 건강한 미래를 응원합니다", date: "2026-08-06" },
  { image: "/assets/scl/news-safety.jpg", title: "안전위기관리 전문 역량 강화 추진", date: "2026-07-10" },
  { image: "/assets/scl/news-gsp.jpg", title: "국내 최초 신생아 선별검사 자동화 플랫폼 'GSP' 도입", date: "2026-06-29" },
];

function Header() {
  return <header className="site-header">
    <a className="logo-link" href="#top" aria-label="SCL 홈"><img src="/assets/scl/scl-logo.svg" alt="SCL" /></a>
    <nav className="desktop-nav" aria-label="주요 메뉴"><a href="#tests">검사</a><a href="#service">고객서비스</a><a href="#business">사업부문</a><a href="#foundation">재단</a></nav>
    <div className="header-tools"><a href="#notices">공문</a><button type="button" aria-label="사이트 검색"><Search size={22} /></button><button type="button" className="language">KOR <ChevronDown size={14} /></button></div>
  </header>;
}

function LegacyPanel() {
  return <aside className="legacy-panel" aria-label="기존 홈페이지 빠른 조회">
    <div className="legacy-block"><h2>결과조회</h2>
      <label><span className="sr-only">아이디</span><input placeholder="아이디" /></label><label><span className="sr-only">비밀번호</span><input type="password" placeholder="비밀번호" /></label>
      <button type="button" className="legacy-primary"><UserRound size={18} /> 로그인</button><p><label><input type="checkbox" /> 아이디 저장</label><span>아이디/비밀번호 찾기</span></p>
    </div>
    <div className="legacy-block legacy-search"><h2>항목조회</h2>
      <label><span className="sr-only">검색 구분</span><select defaultValue="all"><option value="all">전체</option><option>검사명</option><option>보험코드</option><option>검사코드</option></select></label>
      <label><span className="sr-only">검색어</span><input placeholder="검색어를 입력하세요." /></label><button type="button" className="legacy-primary"><Search size={18} /> 검색</button>
    </div>
  </aside>;
}

function Hero() {
  const [slide, setSlide] = useState(0);
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return undefined;
    const id = window.setInterval(() => setSlide((value) => (value + 1) % heroSlides.length), 7000);
    return () => window.clearInterval(id);
  }, [paused]);
  const current = heroSlides[slide];
  const move = (direction) => setSlide((value) => (value + direction + heroSlides.length) % heroSlides.length);
  return <section className="hero" id="top" aria-label="SCL 소개">
    <img className="hero-image" src={current.image} alt="SCL 검사실" /><div className="hero-overlay" />
    <div className="hero-controls" aria-label="메인 배너 제어"><div className="hero-progress"><span style={{ width: `${((slide + 1) / heroSlides.length) * 100}%` }} /></div>
      <strong>{slide + 1} / {heroSlides.length}</strong><button type="button" aria-label="이전 배너" onClick={() => move(-1)}><ArrowLeft /></button><button type="button" aria-label="다음 배너" onClick={() => move(1)}><ArrowRight /></button>
      <button type="button" aria-label={paused ? "배너 재생" : "배너 일시정지"} onClick={() => setPaused((value) => !value)}>{paused ? <Play /> : <Pause />}</button>
    </div><LegacyPanel />
  </section>;
}

function ContentSections() {
  return <main className="home-content">
    <section id="notices" className="content-section notices-section"><div className="section-title"><h2><strong>SCL</strong> 공문</h2><button type="button" aria-label="공문 더보기">+</button></div>
      <div className="notice-list">{notices.map(([date, label, title]) => <a href="#notices" className="notice-row" key={title}><time>{date}</time><strong>{label}</strong><span>{title}</span></a>)}</div>
    </section>
    <section className="content-section"><div className="section-title"><h2><strong>SCL</strong> News</h2><button type="button" aria-label="뉴스 더보기">+</button></div>
      <div className="news-grid">{news.map((item) => <article className="news-card" key={item.title}><img src={item.image} alt="" /><h3>{item.title}</h3><time>{item.date}</time></article>)}</div>
    </section>
    <section className="content-section feature-grid">
      <article><div className="section-title"><h2><strong>SCL</strong> 검사 SpotLight</h2><button type="button">+</button></div><img src="/assets/scl/spotlight-nipt.jpg" alt="태아 DNA 선별검사" /><h3>태아 DNA 선별검사 : 해피버스 & 하모니</h3><p>SCL, 두 가지 다른 방법의 NIPT 검사를 병행하여 선택의 기회를 제공합니다.</p></article>
      <article><div className="section-title"><h2><strong>SCL</strong> 건강레시피</h2><button type="button">+</button></div><img src="/assets/scl/health-recipe.webp" alt="여름철 기침과 몸살 건강 안내" /><h3>여름철 기침, 몸살 '레지오넬라증' 의심하세요!</h3><time>2026-07-24</time></article>
      <article><div className="section-title"><h2><strong>항균제 내성</strong> 소식</h2><button type="button">+</button></div><img src="/assets/scl/resistance-news.png" alt="항균제 내성 소식" /><h3>항균제 내성 소식 Vol.5 No.1</h3><time>2026-02-26</time></article>
    </section>
  </main>;
}

function Footer() {
  return <footer className="site-footer"><div className="footer-links"><span>사이트맵</span><span className="accent">개인정보처리방침</span><span>인트라넷</span><span>원격지원</span></div>
    <div className="footer-main"><img src="/assets/scl/footer-logo.svg" alt="SCL 서울의과학연구소" /><p>경기도 용인시 기흥구 흥덕1로 13 흥덕IT밸리 A동<br />TEL : 1800-0119&nbsp;&nbsp;|&nbsp;&nbsp;FAX : 02)790 - 6509<br />Copyright © Seoul Clinical Laboratories. All rights reserved.</p><strong><small>대표번호</small>1800-0119</strong></div>
  </footer>;
}

export function App() {
  return <div className="prototype-shell"><div className="prototype-badge">SCL 챗봇 시연용 프로토타입</div><Header /><Hero /><ContentSections /><Footer /><ChatWidget /></div>;
}

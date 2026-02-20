import { useState, useRef } from 'react';
import { Activity, Database, HardDrive, RefreshCcw, Download, Terminal, Upload } from 'lucide-react';

function App() {
  const [logMessages, setLogMessages] = useState<string[]>([
    "시스템 초기화 완료.",
    "대기 중...",
  ]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleStartBatch = () => {
    if (!selectedFile) {
      setLogMessages(prev => [...prev, "오류: 처리할 엑셀 파일을 먼저 선택해주세요."]);
      return;
    }
    setIsProcessing(true);
    setLogMessages(prev => [...prev, `${selectedFile.name} 파일 채우기 작업을 시작합니다...`]);
    // Simulate some logs
    setTimeout(() => {
      setLogMessages(prev => [...prev, "브릿지 서버에서 A005930 종목의 분봉 데이터를 수집 중..."]);
    }, 1500);
    setTimeout(() => {
      setLogMessages(prev => [...prev, "데이터 추출 성공. 엑셀 파일을 업데이트합니다..."]);
      setIsProcessing(false);
    }, 4000);
  };

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-200 font-sans selection:bg-blue-500/30">
      {/* Top Navbar */}
      <nav className="sticky top-0 z-50 flex items-center justify-between px-6 py-4 bg-neutral-900 border-b border-neutral-800 shadow-sm">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-blue-600/10 text-blue-500">
            <Activity className="w-5 h-5" />
          </div>
          <h1 className="text-xl font-semibold tracking-tight text-white">퀀트 파이프라인 제어센터</h1>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20">
            <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></div>
            <span className="text-xs font-medium text-emerald-500">대신증권 HTS 연결됨</span>
          </div>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto p-6 grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Sidebar / Quick Stats */}
        <aside className="lg:col-span-3 space-y-4">
          <div className="p-5 rounded-xl border border-neutral-800 bg-neutral-900/50 hover:bg-neutral-900 transition-colors">
            <div className="flex items-center gap-3 mb-2 text-neutral-400">
              <HardDrive className="w-5 h-5" />
              <h3 className="font-medium text-sm">로컬 분봉 캐시</h3>
            </div>
            <p className="text-2xl font-bold text-white">1.2 GB</p>
            <p className="text-xs text-neutral-500 mt-1">2,450개 종목 JSON 보관 중</p>
          </div>

          <div className="p-5 rounded-xl border border-neutral-800 bg-neutral-900/50 hover:bg-neutral-900 transition-colors">
            <div className="flex items-center gap-3 mb-2 text-neutral-400">
              <Database className="w-5 h-5" />
              <h3 className="font-medium text-sm">API 트래픽 제한</h3>
            </div>
            <p className="text-2xl font-bold text-blue-500">59 / 60</p>
            <div className="w-full bg-neutral-800 rounded-full h-1.5 mt-3">
              <div className="bg-blue-500 h-1.5 rounded-full w-[95%]"></div>
            </div>
          </div>
        </aside>

        {/* Main Workspace */}
        <section className="lg:col-span-9 space-y-6">
          {/* Excel Batch Processor */}
          <div className="bg-neutral-900 rounded-2xl border border-neutral-800 overflow-hidden shadow-2xl">
            <div className="px-6 py-5 border-b border-neutral-800 flex items-center justify-between bg-neutral-900/50">
              <h2 className="text-lg font-semibold text-white">엑셀 빈칸 일괄 채우기 모듈</h2>
            </div>
            <div className="p-6">

              <div
                className="border-2 border-dashed border-neutral-700 hover:border-blue-500/50 rounded-xl p-10 flex flex-col items-center justify-center text-center transition-colors mb-6 cursor-pointer bg-neutral-950/50"
                onClick={() => fileInputRef.current?.click()}
              >
                <input
                  type="file"
                  ref={fileInputRef}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) {
                      setSelectedFile(file);
                      setLogMessages(prev => [...prev, `${file.name} 파일이 선택되었습니다.`]);
                    }
                  }}
                  className="hidden"
                  accept=".xlsx, .xls"
                />
                <div className="w-16 h-16 rounded-full bg-blue-500/10 flex items-center justify-center mb-4 text-blue-500">
                  <Upload className="w-8 h-8" />
                </div>
                <h3 className="text-lg font-medium text-white mb-1">
                  {selectedFile ? selectedFile.name : "여기에 object_excel.xlsx 파일을 드래그 앤 드롭하세요"}
                </h3>
                <p className="text-sm text-neutral-500">
                  {selectedFile ? "클릭하여 다른 파일 선택" : "또는 클릭하여 내 PC에서 파일 선택"}
                </p>
              </div>

              <div className="flex justify-end gap-3">
                <button
                  onClick={handleStartBatch}
                  disabled={isProcessing}
                  className="flex items-center gap-2 px-6 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg font-medium transition-all shadow-lg shadow-blue-500/20"
                >
                  {isProcessing ? <RefreshCcw className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                  {isProcessing ? "작업 처리 중..." : "채우기 시작하기"}
                </button>
              </div>

              {/* Terminal Logs */}
              <div className="mt-8">
                <div className="flex items-center gap-2 mb-3 text-neutral-400">
                  <Terminal className="w-4 h-4" />
                  <h3 className="text-sm font-medium">실시간 실행 로그</h3>
                </div>
                <div className="bg-black rounded-xl p-4 font-mono text-sm overflow-y-auto max-h-60 border border-neutral-800 shadow-inner">
                  {logMessages.map((msg, idx) => (
                    <div key={idx} className="mb-1">
                      <span className="text-neutral-500 mr-3">[{new Date().toLocaleTimeString()}]</span>
                      <span className={msg.includes("Error") ? "text-red-400" : msg.includes("complete") ? "text-emerald-400" : "text-neutral-300"}>
                        {msg}
                      </span>
                    </div>
                  ))}
                  {isProcessing && (
                    <div className="flex items-center text-neutral-500 mt-2">
                      <span className="animate-pulse">_</span>
                    </div>
                  )}
                </div>
              </div>

            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

export default App;

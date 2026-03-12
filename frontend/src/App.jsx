import React, { useRef, useState } from "react";
import {
  LayoutDashboard,
  FileText,
  ClipboardCheck,
  Download,
  AlertCircle,
  CheckCircle2,
  Clock,
  User,
  Calendar,
  Layers,
  Mic,
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const formatDisplayValue = (value, fallback = "待定") => {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized || normalized === "tbd" || normalized === "unknown") return fallback;
  if (normalized === "none") return "无";
  return value;
};

const downloadBlob = (blob, filename) => {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.URL.revokeObjectURL(url);
};

const buildFileName = (title, suffix) => {
  const safeTitle = String(title || "会议纪要").replace(/[\\/:*?"<>|]/g, "_").trim();
  return `${safeTitle || "会议纪要"}_${suffix}`;
};

const readErrorMessage = async (response, fallback) => {
  const extractMessage = (payload) => {
    if (!payload) return "";
    if (typeof payload === "string") return payload.trim();
    if (typeof payload?.detail === "string" && payload.detail.trim()) return payload.detail.trim();
    if (typeof payload?.message === "string" && payload.message.trim()) return payload.message.trim();
    return "";
  };

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const payload = await response.json();
    const message = extractMessage(payload);
    if (message) return message;
  }

  const text = await response.text();
  if (!text) return fallback;

  try {
    const payload = JSON.parse(text);
    const message = extractMessage(payload);
    if (message) return message;
  } catch {
    return text;
  }

  return text || fallback;
};

const splitTranscriptParagraphs = (text) => {
  const blocks = String(text || "")
    .split(/\n+/)
    .map((item) => item.trim())
    .filter(Boolean);

  if (blocks.length > 0) return blocks;

  return String(text || "")
    .split(/(?<=[。！？!?])/)
    .map((item) => item.trim())
    .filter(Boolean);
};

const App = () => {
  const fileInputRef = useRef(null);
  const audioInputRef = useRef(null);
  const [activeTab, setActiveTab] = useState("dashboard");
  const [inputText, setInputText] = useState("");
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [reportType, setReportType] = useState("management");
  const [extractedData, setExtractedData] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [error, setError] = useState("");
  const [actionItems, setActionItems] = useState([]);
  const [isDownloading, setIsDownloading] = useState(false);
  const [voiceFile, setVoiceFile] = useState(null);
  const [voiceTitle, setVoiceTitle] = useState("");
  const [voiceTranscript, setVoiceTranscript] = useState("");
  const [voiceMarkdown, setVoiceMarkdown] = useState("");
  const [voiceError, setVoiceError] = useState("");
  const [voiceView, setVoiceView] = useState("transcript");
  const [isTranscribingVoice, setIsTranscribingVoice] = useState(false);
  const [isDownloadingVoice, setIsDownloadingVoice] = useState(false);

  const normalizeActionItems = (actions = []) =>
    actions.map((action, index) => ({
      id: index + 1,
      task: action.task,
      owner: formatDisplayValue(action.owner),
      deadline: formatDisplayValue(action.deadline),
      status: "pending",
      priority: formatDisplayValue(action.risk, "无") !== "无" ? "high" : "medium",
    }));

  const handleFileButtonClick = () => fileInputRef.current?.click();
  const handleAudioFileButtonClick = () => audioInputRef.current?.click();

  const handleFileChange = async (event) => {
    const file = event.target.files?.[0];
    setError("");
    setSelectedFile(file || null);
    if (!file) return;

    const ext = file.name.toLowerCase().split(".").pop();
    if (ext === "txt" || ext === "md") {
      const text = await file.text();
      setInputText(text);
    }
  };

  const handleAudioFileChange = (event) => {
    const file = event.target.files?.[0];
    setVoiceError("");
    setVoiceFile(file || null);
    if (!file) return;

    setVoiceTitle(file.name.replace(/\.[^.]+$/, ""));
    setVoiceTranscript("");
    setVoiceMarkdown("");
    setVoiceView("transcript");
  };

  const handleAnalyze = async () => {
    setIsAnalyzing(true);
    setError("");
    try {
      let response;
      if (selectedFile) {
        const formData = new FormData();
        formData.append("file", selectedFile);
        response = await fetch(`${API_BASE}/api/analyze/upload`, {
          method: "POST",
          body: formData,
        });
      } else {
        response = await fetch(`${API_BASE}/api/analyze/text`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ text: inputText }),
        });
      }

      if (!response.ok) {
        const message = await readErrorMessage(response, "解析失败");
        throw new Error(message || "解析失败");
      }

      const payload = await response.json();
      setExtractedData(payload);
      setActionItems((prev) => [...normalizeActionItems(payload.actions), ...prev].slice(0, 12));
    } catch (requestError) {
      setError(requestError.message || "服务异常，请稍后重试");
    } finally {
      setIsAnalyzing(false);
    }
  };

  const buildReportPayload = () => {
    if (!extractedData) return null;
    return {
      title: extractedData.title,
      date: extractedData.date,
      weekly_period: extractedData.weekly_period,
      decisions: extractedData.decisions || [],
      actions: (extractedData.actions || []).map((action) => ({
        task: action.task,
        owner: formatDisplayValue(action.owner),
        deadline: formatDisplayValue(action.deadline),
        risk: formatDisplayValue(action.risk, "无"),
      })),
      risks: extractedData.risks || [],
      report_type: reportType,
    };
  };

  const handleDownload = async (type) => {
    const payload = buildReportPayload();
    if (!payload) {
      setError("当前没有可导出的解析结果。");
      return;
    }

    setIsDownloading(true);
    setError("");

    try {
      const endpoint = type === "docx" ? "/api/report/weekly/docx" : "/api/report/weekly/markdown";
      const response = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const message = await readErrorMessage(response, "导出失败");
        throw new Error(message || "导出失败");
      }

      const blob = await response.blob();
      const filename = buildFileName(payload.title, type === "docx" ? `${reportType}.docx` : `${reportType}.md`);
      downloadBlob(blob, filename);
    } catch (requestError) {
      setError(requestError.message || "导出失败，请稍后重试");
    } finally {
      setIsDownloading(false);
    }
  };

  const buildVoicePayload = () => {
    if (!voiceTranscript.trim()) return null;
    return {
      title: voiceTitle || voiceFile?.name?.replace(/\.[^.]+$/, "") || "会议语音转写",
      transcript_text: voiceTranscript,
    };
  };

  const handleVoiceTranscribe = async () => {
    if (!voiceFile) {
      setVoiceError("请先选择音频文件。");
      return;
    }

    setIsTranscribingVoice(true);
    setVoiceError("");

    try {
      const formData = new FormData();
      formData.append("file", voiceFile);

      const response = await fetch(`${API_BASE}/api/audio/transcribe`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const message = await readErrorMessage(response, "语音转写失败");
        throw new Error(message || "语音转写失败");
      }

      const payload = await response.json();
      setVoiceTitle(payload.title || voiceFile.name.replace(/\.[^.]+$/, ""));
      setVoiceTranscript(payload.transcript_text || "");
      setVoiceMarkdown(payload.markdown || "");
      setVoiceView("transcript");
    } catch (requestError) {
      setVoiceError(requestError.message || "语音转写失败，请稍后重试");
    } finally {
      setIsTranscribingVoice(false);
    }
  };

  const handleVoiceDownload = async (type) => {
    const payload = buildVoicePayload();
    if (!payload) {
      setVoiceError("当前没有可导出的转写内容。");
      return;
    }

    setIsDownloadingVoice(true);
    setVoiceError("");

    try {
      const endpoint = type === "docx" ? "/api/audio/transcribe/docx" : "/api/audio/transcribe/markdown";
      const response = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const message = await readErrorMessage(response, "下载失败");
        throw new Error(message || "下载失败");
      }

      const blob = await response.blob();
      const filename = buildFileName(payload.title, type === "docx" ? "转写稿.docx" : "转写稿.md");
      downloadBlob(blob, filename);
    } catch (requestError) {
      setVoiceError(requestError.message || "下载失败，请稍后重试");
    } finally {
      setIsDownloadingVoice(false);
    }
  };

  const transcriptParagraphs = splitTranscriptParagraphs(voiceTranscript);

  const renderAnalysisPreview = () => {
    if (!extractedData) {
      return (
        <div className="h-full min-h-[360px] flex flex-col items-center justify-center text-center text-slate-400">
          <FileText size={42} className="mb-4 text-slate-300" />
          <p className="text-lg font-semibold text-slate-500">完成解析后，这里会直接显示纪要预览</p>
          <p className="text-sm mt-2">上方粘贴会议内容或上传文档后，点击“开始解析”即可生成结构化结果。</p>
        </div>
      );
    }

    return (
      <div className="space-y-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h3 className="text-xl font-bold text-slate-900">解析结果预览</h3>
            <p className="text-sm text-slate-500 mt-1">
              {extractedData.title || "会议纪要预览"}
              {extractedData.date ? ` · ${extractedData.date}` : ""}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <div className="flex space-x-2 bg-slate-200 p-1 rounded-lg">
              <button
                onClick={() => setReportType("management")}
                className={`px-4 py-1.5 text-sm rounded-md transition-all ${
                  reportType === "management" ? "bg-white shadow-sm font-bold" : "text-slate-600"
                }`}
              >
                管理版周报
              </button>
              <button
                onClick={() => setReportType("project")}
                className={`px-4 py-1.5 text-sm rounded-md transition-all ${
                  reportType === "project" ? "bg-white shadow-sm font-bold" : "text-slate-600"
                }`}
              >
                项目版纪要
              </button>
            </div>

            <button
              onClick={() => handleDownload("docx")}
              disabled={isDownloading}
              className="px-4 py-2 rounded-xl bg-slate-100 text-sm font-medium text-slate-700 hover:bg-slate-200 transition-colors disabled:cursor-not-allowed disabled:opacity-60"
            >
              下载 Word
            </button>
            <button
              onClick={() => handleDownload("markdown")}
              disabled={isDownloading}
              className="px-4 py-2 rounded-xl bg-slate-100 text-sm font-medium text-slate-700 hover:bg-slate-200 transition-colors disabled:cursor-not-allowed disabled:opacity-60"
            >
              下载 Markdown
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
          <div className="xl:col-span-2">
            <div className="bg-white p-8 rounded-2xl border border-slate-200 shadow-sm min-h-[560px]">
              {reportType === "management" ? (
                <div className="space-y-6">
                  <div className="border-b pb-4">
                    <h1 className="text-2xl font-bold text-center mb-2">项目周报</h1>
                    <p className="text-center text-slate-400 text-sm">{extractedData.weekly_period}</p>
                  </div>
                  <div>
                    <h4 className="font-bold flex items-center text-blue-700 mb-2">
                      <CheckCircle2 size={18} className="mr-2" />
                      决策项
                    </h4>
                    <ul className="list-disc list-inside space-y-1 text-slate-700 text-sm pl-2">
                      {(extractedData.decisions || []).map((decision, index) => (
                        <li key={`${decision}-${index}`}>{decision}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h4 className="font-bold flex items-center text-indigo-700 mb-2">
                      <Layers size={18} className="mr-2" />
                      行动项
                    </h4>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-slate-50 text-slate-500">
                          <th className="text-left py-2 px-3 font-medium">任务</th>
                          <th className="text-left py-2 px-3 font-medium">负责人</th>
                          <th className="text-left py-2 px-3 font-medium">截止日期</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(extractedData.actions || []).map((action, index) => (
                          <tr key={`${action.task}-${index}`} className="border-b border-slate-50">
                            <td className="py-3 px-3">{action.task}</td>
                            <td className="py-3 px-3 font-medium">{formatDisplayValue(action.owner)}</td>
                            <td className="py-3 px-3 text-slate-500">{formatDisplayValue(action.deadline)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="bg-red-50 p-4 rounded-xl border border-red-100">
                    <h4 className="font-bold flex items-center text-red-700 mb-1 text-sm">
                      <AlertCircle size={16} className="mr-2" />
                      风险项
                    </h4>
                    <ul className="list-disc list-inside text-red-600 text-xs pl-2">
                      {(extractedData.risks || []).map((risk, index) => (
                        <li key={`${risk}-${index}`}>{risk}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              ) : (
                <div className="space-y-6">
                  <div className="flex justify-between items-start">
                    <h1 className="text-xl font-bold text-slate-800">{extractedData.title}</h1>
                    <span className="bg-blue-100 text-blue-700 text-xs px-2 py-1 rounded">会议纪要</span>
                  </div>
                  <div className="grid grid-cols-2 gap-4 text-xs text-slate-500">
                    <div className="flex items-center space-x-2">
                      <Calendar size={14} /> <span>日期：{extractedData.date}</span>
                    </div>
                    <div className="flex items-center space-x-2">
                      <User size={14} /> <span>记录人：AI 助手</span>
                    </div>
                  </div>
                  <hr className="border-slate-100" />
                  <div>
                    <p className="text-sm font-bold mb-2">行动摘要</p>
                    <div className="space-y-4">
                      {(extractedData.actions || []).map((action, index) => (
                        <div key={`${action.task}-${index}`} className="flex space-x-3 items-start border-l-2 border-blue-500 pl-4 py-1">
                          <div>
                            <p className="text-sm font-medium">{action.task}</p>
                            <div className="flex space-x-4 mt-1 text-xs text-slate-400">
                              <span>{formatDisplayValue(action.owner)}</span>
                              <span>{formatDisplayValue(action.deadline)}</span>
                              {formatDisplayValue(action.risk, "无") !== "无" && (
                                <span className="text-orange-500">{formatDisplayValue(action.risk, "无")}</span>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="space-y-4">
            <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
              <h4 className="font-bold mb-4 flex items-center text-sm">
                <Download size={16} className="mr-2 text-blue-600" />
                提取结果
              </h4>
              <div className="space-y-3 text-sm text-slate-600">
                <div className="flex items-center justify-between rounded-xl bg-slate-50 px-4 py-3">
                  <span>决策项</span>
                  <span className="font-semibold text-slate-900">{(extractedData.decisions || []).length}</span>
                </div>
                <div className="flex items-center justify-between rounded-xl bg-slate-50 px-4 py-3">
                  <span>行动项</span>
                  <span className="font-semibold text-slate-900">{(extractedData.actions || []).length}</span>
                </div>
                <div className="flex items-center justify-between rounded-xl bg-slate-50 px-4 py-3">
                  <span>风险项</span>
                  <span className="font-semibold text-slate-900">{(extractedData.risks || []).length}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  };

  const SidebarItem = ({ id, icon: Icon, label }) => (
    <button
      onClick={() => setActiveTab(id)}
      className={`w-full flex items-center space-x-3 px-4 py-3 rounded-lg transition-colors ${
        activeTab === id ? "bg-blue-600 text-white shadow-lg" : "text-slate-600 hover:bg-slate-100"
      }`}
    >
      <Icon size={20} />
      <span className="font-medium">{label}</span>
    </button>
  );

  return (
    <div className="flex h-screen bg-slate-50 text-slate-900 font-sans">
      <aside className="w-64 bg-white border-r border-slate-200 flex flex-col p-4">
        <div className="flex items-center space-x-2 px-4 mb-8">
          <div className="bg-blue-600 p-1.5 rounded-lg">
            <ClipboardCheck className="text-white" size={24} />
          </div>
          <h1 className="text-xl font-bold bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
            会议纪要助手
          </h1>
        </div>

        <nav className="flex-1 space-y-1">
          <SidebarItem id="dashboard" icon={LayoutDashboard} label="工作台" />
          <SidebarItem id="new" icon={FileText} label="新建纪要" />
          <SidebarItem id="voice" icon={Mic} label="会议语音" />
          <SidebarItem id="tracking" icon={Clock} label="行动追踪" />
        </nav>
      </aside>

      <main className="flex-1 overflow-y-auto p-8">
        {activeTab === "dashboard" && (
          <div className="max-w-5xl mx-auto animate-fade-in">
            <div className="mb-8">
              <h2 className="text-2xl font-bold mb-2">欢迎回来</h2>
              <p className="text-slate-500">这里展示当前会议解析与行动项状态。</p>
            </div>

            <div className="grid grid-cols-3 gap-6 mb-8">
              <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
                <div className="flex justify-between items-start mb-4">
                  <div className="bg-orange-100 p-2 rounded-lg text-orange-600">
                    <AlertCircle size={24} />
                  </div>
                  <span className="text-xs font-bold text-slate-400">已逾期</span>
                </div>
                <h3 className="text-3xl font-bold">{actionItems.filter((x) => x.status === "overdue").length}</h3>
                <p className="text-sm text-slate-500 mt-1">需要立即跟进的行动项</p>
              </div>
              <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
                <div className="flex justify-between items-start mb-4">
                  <div className="bg-blue-100 p-2 rounded-lg text-blue-600">
                    <Clock size={24} />
                  </div>
                  <span className="text-xs font-bold text-slate-400">进行中</span>
                </div>
                <h3 className="text-3xl font-bold">{actionItems.filter((x) => x.status === "pending").length}</h3>
                <p className="text-sm text-slate-500 mt-1">本周待处理事项</p>
              </div>
              <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
                <div className="flex justify-between items-start mb-4">
                  <div className="bg-green-100 p-2 rounded-lg text-green-600">
                    <CheckCircle2 size={24} />
                  </div>
                  <span className="text-xs font-bold text-slate-400">已完成</span>
                </div>
                <h3 className="text-3xl font-bold">{actionItems.filter((x) => x.status === "completed").length}</h3>
                <p className="text-sm text-slate-500 mt-1">最近 7 天已完成事项</p>
              </div>
            </div>
          </div>
        )}

        {activeTab === "new" && (
          <div className="max-w-6xl mx-auto space-y-6 animate-fade-in">
            <div>
              <h2 className="text-2xl font-bold">生成会议纪要</h2>
              <p className="text-slate-500 mt-1">上方输入会议文本或上传文档，下方直接查看解析结果预览。</p>
            </div>

            <div className="bg-white rounded-[28px] border border-slate-200 shadow-sm p-6 md:p-8 space-y-6">
              <div className="rounded-[24px] border border-slate-200 overflow-hidden bg-white">
                <div className="px-6 py-4 border-b border-slate-100">
                  <p className="text-sm font-medium text-slate-500">会议文本输入区</p>
                </div>
                <div className="p-6 bg-slate-50">
                  <textarea
                    value={inputText}
                    onChange={(e) => setInputText(e.target.value)}
                    placeholder="请在此粘贴会议转写文本，或上传 txt / md / docx 文件。"
                    className="w-full min-h-[240px] rounded-2xl border border-slate-200 bg-white p-6 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none text-slate-700 leading-relaxed shadow-sm"
                  />
                </div>
              </div>

              <div className="rounded-[24px] border border-slate-200 bg-gradient-to-r from-slate-100 via-white to-slate-100 p-6">
                <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-6">
                  <div className="flex items-start gap-4">
                    <div className="w-14 h-14 rounded-2xl bg-blue-100 text-blue-600 flex items-center justify-center shrink-0">
                      <FileText size={26} />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">生成会议纪要</p>
                      <h3 className="text-xl font-bold text-slate-900 mt-1">上传文档或粘贴会议文本</h3>
                      <p className="text-sm text-slate-500 mt-2">支持 txt、md、docx 格式，解析完成后会在下方直接展示结构化预览。</p>
                      <p className="text-sm text-slate-700 mt-3">当前文件：{selectedFile ? selectedFile.name : "尚未选择会议文档"}</p>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-3">
                    <button
                      onClick={handleFileButtonClick}
                      className="px-5 py-3 rounded-2xl bg-slate-900 text-white text-sm font-semibold hover:bg-slate-800 transition-colors"
                    >
                      添加会议文档
                    </button>
                    <button
                      onClick={handleAnalyze}
                      disabled={(!inputText && !selectedFile) || isAnalyzing}
                      className={`px-5 py-3 rounded-2xl text-sm font-semibold transition-colors ${
                        (!inputText && !selectedFile) || isAnalyzing
                          ? "bg-slate-200 text-slate-500 cursor-not-allowed"
                          : "bg-blue-600 text-white hover:bg-blue-700"
                      }`}
                    >
                      {isAnalyzing ? "正在解析..." : "开始解析"}
                    </button>
                  </div>
                </div>

                <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  accept=".txt,.md,.docx"
                  onChange={handleFileChange}
                />
              </div>

              {!extractedData && error && (
                <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {error}
                </div>
              )}

              <div className="rounded-[24px] border border-slate-200 overflow-hidden bg-white">
                <div className="px-6 py-4 border-b border-slate-100">
                  <p className="text-sm font-medium text-slate-500">解析结果预览</p>
                </div>
                <div className="p-6 md:p-8 min-h-[420px] bg-slate-50">
                  {renderAnalysisPreview()}
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === "voice" && (
          <div className="max-w-6xl mx-auto space-y-6 animate-fade-in">
            <div>
              <h2 className="text-2xl font-bold">会议语音</h2>
              <p className="text-slate-500 mt-1">上传会议音频后，系统会完成语音转写，并支持 Word 与 Markdown 下载。</p>
            </div>

            <div className="bg-white rounded-[28px] border border-slate-200 shadow-sm p-6 md:p-8 space-y-6">
              <div className="rounded-[24px] border border-slate-200 bg-gradient-to-r from-slate-100 via-white to-slate-100 p-6">
                <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-6">
                  <div className="flex items-start gap-4">
                    <div className="w-14 h-14 rounded-2xl bg-blue-100 text-blue-600 flex items-center justify-center shrink-0">
                      <Mic size={26} />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">上传会议语音</p>
                      <h3 className="text-xl font-bold text-slate-900 mt-1">将语音文件转换为文本</h3>
                      <p className="text-sm text-slate-500 mt-2">支持 mp3、wav、m4a、mp4、aac、ogg、webm 格式。</p>
                      <p className="text-sm text-slate-700 mt-3">当前文件：{voiceFile ? voiceFile.name : "尚未选择音频文件"}</p>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-3">
                    <button
                      onClick={handleAudioFileButtonClick}
                      className="px-5 py-3 rounded-2xl bg-slate-900 text-white text-sm font-semibold hover:bg-slate-800 transition-colors"
                    >
                      添加语音文件
                    </button>
                    <button
                      onClick={handleVoiceTranscribe}
                      disabled={!voiceFile || isTranscribingVoice}
                      className={`px-5 py-3 rounded-2xl text-sm font-semibold transition-colors ${
                        !voiceFile || isTranscribingVoice
                          ? "bg-slate-200 text-slate-500 cursor-not-allowed"
                          : "bg-blue-600 text-white hover:bg-blue-700"
                      }`}
                    >
                      {isTranscribingVoice ? "正在转写..." : "开始转写"}
                    </button>
                  </div>
                </div>

                <input
                  ref={audioInputRef}
                  type="file"
                  className="hidden"
                  accept=".mp3,.wav,.m4a,.mp4,.aac,.ogg,.webm"
                  onChange={handleAudioFileChange}
                />
              </div>

              {voiceError && (
                <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {voiceError}
                </div>
              )}

              <div className="rounded-[24px] border border-slate-200 overflow-hidden bg-white">
                <div className="px-6 py-4 border-b border-slate-100 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
                  <div className="flex items-center gap-6 text-sm font-medium">
                    <button
                      onClick={() => setVoiceView("transcript")}
                      className={`pb-2 border-b-2 transition-colors ${
                        voiceView === "transcript" ? "border-slate-900 text-slate-900" : "border-transparent text-slate-400"
                      }`}
                    >
                      转写
                    </button>
                    <button
                      onClick={() => setVoiceView("markdown")}
                      className={`pb-2 border-b-2 transition-colors ${
                        voiceView === "markdown" ? "border-slate-900 text-slate-900" : "border-transparent text-slate-400"
                      }`}
                    >
                      Markdown 预览
                    </button>
                  </div>

                  <div className="flex flex-wrap gap-3">
                    <button
                      onClick={() => handleVoiceDownload("docx")}
                      disabled={!voiceTranscript || isDownloadingVoice}
                      className="px-4 py-2 rounded-xl bg-slate-100 text-sm font-medium text-slate-700 hover:bg-slate-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      下载 Word
                    </button>
                    <button
                      onClick={() => handleVoiceDownload("markdown")}
                      disabled={!voiceTranscript || isDownloadingVoice}
                      className="px-4 py-2 rounded-xl bg-slate-100 text-sm font-medium text-slate-700 hover:bg-slate-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      下载 Markdown
                    </button>
                  </div>
                </div>

                <div className="p-6 md:p-8 min-h-[420px] max-h-[700px] overflow-y-auto bg-slate-50">
                  {!voiceTranscript ? (
                    <div className="h-full min-h-[320px] flex flex-col items-center justify-center text-center text-slate-400">
                      <Mic size={42} className="mb-4 text-slate-300" />
                      <p className="text-lg font-semibold text-slate-500">上传音频后，这里会直接显示转写内容</p>
                      <p className="text-sm mt-2">页面上方选择音频文件后即可开始转写。</p>
                    </div>
                  ) : voiceView === "transcript" ? (
                    <div className="space-y-6">
                      {transcriptParagraphs.map((paragraph, index) => (
                        <div key={`${paragraph}-${index}`} className="bg-white rounded-2xl border border-slate-200 px-5 py-4 shadow-sm">
                          <div className="flex items-center gap-3 mb-3">
                            <span className="inline-flex items-center justify-center h-8 px-3 rounded-xl bg-blue-100 text-blue-700 text-xs font-semibold">
                              段落 {String(index + 1).padStart(2, "0")}
                            </span>
                            <span className="text-xs text-slate-400">{voiceTitle || "会议语音转写"}</span>
                          </div>
                          <p className="text-base leading-8 text-slate-800 whitespace-pre-wrap">{paragraph}</p>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <pre className="whitespace-pre-wrap text-sm leading-7 text-slate-700 font-mono bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
                      {voiceMarkdown}
                    </pre>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === "analysis" && (
          <div className="max-w-6xl mx-auto space-y-6 animate-fade-in">
            {renderAnalysisPreview()}
          </div>
        )}

        {activeTab === "tracking" && (
          <div className="max-w-5xl mx-auto animate-slide-in-from-bottom-4">
            <div className="mb-8 flex justify-between items-center">
              <div>
                <h2 className="text-2xl font-bold mb-1 text-slate-800">行动追踪</h2>
                <p className="text-slate-500">这里展示从会议纪要中提取出的行动项。</p>
              </div>
            </div>

            <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
              <table className="w-full text-left">
                <thead className="bg-slate-50 border-b border-slate-100">
                  <tr>
                    <th className="py-4 px-6 text-xs font-bold text-slate-500 uppercase">任务</th>
                    <th className="py-4 px-6 text-xs font-bold text-slate-500 uppercase">负责人</th>
                    <th className="py-4 px-6 text-xs font-bold text-slate-500 uppercase">截止日期</th>
                    <th className="py-4 px-6 text-xs font-bold text-slate-500 uppercase">状态</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {actionItems.length === 0 ? (
                    <tr>
                      <td className="px-6 py-8 text-sm text-slate-400" colSpan={4}>
                        暂无行动项，请先完成一次会议解析。
                      </td>
                    </tr>
                  ) : (
                    actionItems.map((item) => (
                    <tr key={item.id} className="hover:bg-slate-50 transition-colors">
                      <td className="py-4 px-6 text-sm font-medium">{item.task}</td>
                      <td className="py-4 px-6 text-sm text-slate-600">{item.owner}</td>
                      <td className="py-4 px-6 text-sm text-slate-500">{item.deadline}</td>
                      <td className="py-4 px-6 text-sm text-slate-500">
                        {item.status === "completed" ? "已完成" : item.status === "overdue" ? "已逾期" : "进行中"}
                      </td>
                    </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </main>
    </div>
  );
};

export default App;

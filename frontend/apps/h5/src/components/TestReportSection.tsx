"use client";

import { useState } from "react";

interface Report {
  id: string;
  title: string;
  summary?: string;
  image_url?: string;
  file_url?: string;
  date?: string;
}

interface TestReportSectionProps {
  /** 检测报告列表 */
  reports: Report[];
}

/**
 * 检测报告展示组件
 *
 * 以卡片列表形式展示检测报告，包含标题、日期、摘要。
 * 支持图片缩略图点击放大查看，附件提供下载链接。
 */
export function TestReportSection({ reports }: TestReportSectionProps) {
  const [expandedImage, setExpandedImage] = useState<string | null>(null);

  if (!reports.length) {
    return null;
  }

  return (
    <>
      <section className="space-y-3">
        <h3 className="text-base font-semibold text-gray-900">检测报告</h3>
        <div className="space-y-3">
          {reports.map((report) => (
            <div
              key={report.id}
              className="rounded-2xl bg-white p-4 shadow-sm"
            >
              {/* 报告标题 + 日期 */}
              <div className="flex items-start justify-between gap-2">
                <h4 className="text-sm font-semibold text-gray-900">
                  {report.title}
                </h4>
                {report.date && (
                  <span className="shrink-0 text-xs text-gray-400">
                    {report.date}
                  </span>
                )}
              </div>

              {/* 摘要 */}
              {report.summary && (
                <p className="mt-1.5 text-sm text-gray-600 line-clamp-3">
                  {report.summary}
                </p>
              )}

              {/* 缩略图 */}
              {report.image_url && (
                <button
                  type="button"
                  onClick={() => setExpandedImage(report.image_url!)}
                  className="mt-3 block overflow-hidden rounded-xl"
                >
                  <img
                    src={report.image_url}
                    alt={report.title}
                    className="h-32 w-full object-cover transition-transform active:scale-[1.02]"
                  />
                </button>
              )}

              {/* 附件下载 */}
              {report.file_url && (
                <a
                  href={report.file_url}
                  download
                  className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-gray-50 px-3 py-1.5 text-xs font-medium text-blue-600 active:bg-gray-100"
                >
                  <svg
                    className="h-3.5 w-3.5"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M12 4v12m0 0l-4-4m4 4l4-4M4 18h16"
                    />
                  </svg>
                  下载报告
                </a>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* 图片放大查看弹窗 */}
      {expandedImage && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
          onClick={() => setExpandedImage(null)}
          role="dialog"
          aria-label="放大查看图片"
        >
          <div className="relative mx-4 max-h-[90vh] max-w-md overflow-hidden rounded-2xl">
            <img
              src={expandedImage}
              alt="检测报告放大图"
              className="h-auto max-h-[90vh] w-full object-contain"
            />
          </div>
          {/* 关闭提示 */}
          <div className="absolute bottom-8 left-0 right-0 text-center">
            <span className="rounded-full bg-white/20 px-4 py-1.5 text-xs text-white backdrop-blur-sm">
              点击任意位置关闭
            </span>
          </div>
        </div>
      )}
    </>
  );
}

"use client";

import { Download } from "lucide-react";
import { useState } from "react";
import { safePublicUrl } from "@/lib/public-url";

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
        <h3 className="text-base font-semibold text-foreground">检测报告</h3>
        <div className="space-y-3">
          {reports.map((report) => {
            const imageUrl = safePublicUrl(report.image_url);
            const fileUrl = safePublicUrl(report.file_url);
            return (
              <div
                key={report.id}
                className="rounded-2xl bg-surface p-4 shadow-sm"
              >
                {/* 报告标题 + 日期 */}
                <div className="flex items-start justify-between gap-2">
                  <h4 className="text-sm font-semibold text-foreground">
                    {report.title}
                  </h4>
                  {report.date && (
                    <span className="shrink-0 text-xs text-foreground-tertiary">
                      {report.date}
                    </span>
                  )}
                </div>

                {/* 摘要 */}
                {report.summary && (
                  <p className="mt-1.5 text-sm text-foreground-secondary line-clamp-3">
                    {report.summary}
                  </p>
                )}

                {/* 缩略图 */}
                {imageUrl && (
                  <button
                    type="button"
                    onClick={() => setExpandedImage(imageUrl)}
                    className="mt-3 block overflow-hidden rounded-xl"
                  >
                    <img
                      src={imageUrl}
                      alt={report.title}
                      // eslint-disable-next-line tailwindcss/no-arbitrary-value -- 批0 外存量违规，待后续批次收敛（微交互缩放）
                      className="h-32 w-full object-cover transition-transform active:scale-[1.02]"
                    />
                  </button>
                )}

                {/* 附件下载 */}
                {fileUrl && (
                  <a
                    href={fileUrl}
                    download
                    className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-muted px-3 py-1.5 text-xs font-medium text-link active:bg-muted"
                  >
                    <Download className="h-3.5 w-3.5" aria-hidden="true" />
                    下载报告
                  </a>
                )}
              </div>
            );
          })}
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
          {/* eslint-disable-next-line tailwindcss/no-arbitrary-value -- 批0 外存量违规，待后续批次收敛（弹窗视口高度上限） */}
          <div className="relative mx-4 max-h-[90vh] max-w-md overflow-hidden rounded-2xl">
            <img
              src={expandedImage}
              alt="检测报告放大图"
              // eslint-disable-next-line tailwindcss/no-arbitrary-value -- 批0 外存量违规，待后续批次收敛（弹窗视口高度上限）
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

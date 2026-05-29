"use client";

interface OuterCodeGuideProps {
  brandName: string;
  productName?: string;
  productImage?: string;
  innerCodeHint?: string;
}

export function OuterCodeGuide({
  brandName,
  productName,
  productImage,
  innerCodeHint,
}: OuterCodeGuideProps) {
  return (
    <div className="rounded-2xl bg-white p-4 shadow-sm">
      <div className="flex items-center gap-2 mb-3">
        <span className="inline-flex items-center rounded-full bg-blue-100 px-2.5 py-0.5 text-xs font-medium text-blue-700">
          外码
        </span>
        <span className="text-sm font-semibold text-gray-900">{brandName}</span>
      </div>

      {productImage && (
        <img
          src={productImage}
          alt={productName || ""}
          className="mb-3 h-40 w-full rounded-xl object-cover"
        />
      )}

      {productName && (
        <h2 className="text-base font-bold text-gray-900">{productName}</h2>
      )}

      <div className="mt-3 rounded-xl bg-amber-50 p-3">
        <div className="flex items-start gap-2">
          <span className="text-lg">👆</span>
          <div>
            <p className="text-sm font-medium text-amber-800">
              {innerCodeHint || "刮开内码查看验真信息"}
            </p>
            <p className="mt-1 text-xs text-amber-600">
              请找到包装上的内码标签，刮开涂层后扫码验真
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

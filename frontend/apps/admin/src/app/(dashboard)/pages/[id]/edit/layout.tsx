export default function EditLayout({ children }: { children: React.ReactNode }) {
  return <div className="fixed inset-0 z-50 bg-gray-50">{children}</div>;
}

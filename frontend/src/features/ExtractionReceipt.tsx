import type { ExtractionReceiptDto } from '../types';

export function ExtractionReceipt({ result }: { result: ExtractionReceiptDto }) {
  return <div role="status" className="panel" aria-label="AMD extraction result">
    <h3>AMD extraction: {result.status}</h3>
    <p>{result.accepted_count} accepted, {result.rejected_count} rejected. Accepted claims remain unverified until human review.</p>
    {result.receipt && <>
      <p>Provider: {result.receipt.provider} · Model: {result.receipt.model_id}</p>
      <p>Completed: {result.receipt.timestamp} · Latency: {result.receipt.latency_ms} ms</p>
      <p>Receipt: {result.receipt_id} · Input/output schema: {result.receipt.input_schema_version}/{result.receipt.output_schema_version}</p>
    </>}
    {!result.receipt && <p>No completed provider receipt was recorded.</p>}
  </div>;
}

export const label = (value: string) =>
  value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
export const date = (value: string | null | undefined) =>
  value
    ? new Date(value).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
export const shortId = (value: string | null | undefined) =>
  value ? value.slice(0, 8) : "—";
export const inputDate = (value: FormDataEntryValue | null) =>
  value ? new Date(String(value)).toISOString() : undefined;
export function formValues(form: HTMLFormElement) {
  return new FormData(form);
}
export const uuid = () => crypto.randomUUID();

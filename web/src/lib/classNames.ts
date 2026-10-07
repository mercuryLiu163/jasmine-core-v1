/** Tiny class-name joiner; avoids a dependency for one line of logic. */
export function cls(...values: ReadonlyArray<string | false | null | undefined>): string {
  return values.filter((value): value is string => Boolean(value)).join(' ')
}

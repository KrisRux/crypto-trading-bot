import { createContext, useContext } from 'react'
import { Lang, t, TranslationKey } from '../i18n'

export const LangContext = createContext<{
  lang: Lang
  setLang: (l: Lang) => void
}>({ lang: 'it', setLang: () => {} })

/** Hook that returns the current language and a translate function. */
export function useLang() {
  const { lang, setLang } = useContext(LangContext)
  const tr = (key: TranslationKey) => t(key, lang)
  /** Inline bilingual shorthand — prefer i18n keys when possible. */
  const l = (it: string, en: string) => (lang === 'it' ? it : en)
  return { lang, setLang, t: tr, l }
}

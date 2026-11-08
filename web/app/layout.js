import './styles.css';

export const metadata = {
  title: 'Replyline — Reply to every Google review with care',
  description: 'Write thoughtful Google review replies in minutes. Draft, edit, approve, and post from your verified Business Profile.',
};

export default function RootLayout({ children }) {
  return <html lang="en"><body>{children}</body></html>;
}

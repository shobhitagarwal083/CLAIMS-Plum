import styles from './Footer.module.css';

export default function Footer() {
  return (
    <footer className={styles.footer}>
      <div className={styles.container}>
        <div className={styles.left}>
          <h4 className={styles.title}>Plum claims orchestration</h4>
          <p className={styles.text}>
            An intelligent, multi-agent health insurance claims adjudication platform with deep explainability and robust error degradation.
          </p>
        </div>
        <div className={styles.right}>
        </div>
      </div>
    </footer>
  );
}

import javax.swing.*;
import javax.swing.border.LineBorder;
import javax.swing.text.*;
import java.awt.*;
import java.awt.event.*;
import java.io.*;
import java.net.*;
import java.util.*;
import java.util.regex.*;

public class SubnetClient {
    private JFrame frame;
    private JTextPane chatPane;
    private JTextField inputField;
    private JLabel statusLabel;

    private Socket socket;
    private BufferedReader in;
    private PrintWriter out;
    private Thread listenerThread;

    private String nickname = "guest";

    private static final Map<String, Color> ANSI_COLORS = Map.ofEntries(
            Map.entry("30", Color.BLACK),
            Map.entry("31", new Color(255, 80, 80)),
            Map.entry("32", new Color(80, 255, 80)),
            Map.entry("33", new Color(255, 255, 80)),
            Map.entry("34", new Color(80, 80, 255)),
            Map.entry("35", new Color(255, 80, 255)),
            Map.entry("36", new Color(80, 255, 255)),
            Map.entry("37", Color.WHITE),
            Map.entry("90", Color.GRAY),
            Map.entry("91", new Color(255, 150, 150)),
            Map.entry("92", new Color(150, 255, 150)),
            Map.entry("93", new Color(255, 255, 150)),
            Map.entry("94", new Color(150, 150, 255)),
            Map.entry("95", new Color(255, 150, 255)),
            Map.entry("96", new Color(150, 255, 255)),
            Map.entry("97", Color.WHITE)
    );

    public SubnetClient() {
        initUI();
    }

    private void initUI() {
        frame = new JFrame("SUBNET CLIENT");
        frame.setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE);
        frame.setSize(900, 700);
        frame.setLayout(new BorderLayout());

        JPanel topPanel = new JPanel(new BorderLayout());
        topPanel.setBackground(Color.DARK_GRAY);
        topPanel.setBorder(new LineBorder(Color.BLACK, 3));

        JLabel title = new JLabel(" SUBNET CLIENT — LIVE NODE ");
        title.setFont(new Font("Monospaced", Font.BOLD, 16));
        title.setForeground(Color.GREEN);
        topPanel.add(title, BorderLayout.WEST);

        statusLabel = new JLabel("Disconnected", JLabel.RIGHT);
        statusLabel.setFont(new Font("Monospaced", Font.PLAIN, 14));
        statusLabel.setForeground(Color.LIGHT_GRAY);
        topPanel.add(statusLabel, BorderLayout.EAST);

        chatPane = new JTextPane();
        chatPane.setEditable(false);
        chatPane.setBackground(Color.BLACK);
        chatPane.setForeground(Color.WHITE);
        chatPane.setFont(new Font("Monospaced", Font.PLAIN, 14));

        JScrollPane scrollPane = new JScrollPane(chatPane);
        scrollPane.setBorder(new LineBorder(Color.BLACK, 3));

        inputField = new JTextField();
        inputField.setBackground(Color.BLACK);
        inputField.setForeground(Color.GREEN);
        inputField.setCaretColor(Color.GREEN);
        inputField.setFont(new Font("Monospaced", Font.PLAIN, 14));
        inputField.setBorder(new LineBorder(Color.GREEN, 2));
        inputField.addActionListener(e -> sendMessage());

        frame.add(topPanel, BorderLayout.NORTH);
        frame.add(scrollPane, BorderLayout.CENTER);
        frame.add(inputField, BorderLayout.SOUTH);

        JMenuBar menuBar = new JMenuBar();
        JMenu menu = new JMenu("File");
        JMenuItem connectItem = new JMenuItem("Connect");
        JMenuItem quitItem = new JMenuItem("Quit");

        connectItem.addActionListener(e -> showConnectDialog());
        quitItem.addActionListener(e -> System.exit(0));
        menu.add(connectItem);
        menu.add(quitItem);
        menuBar.add(menu);

        frame.setJMenuBar(menuBar);
        frame.setVisible(true);
    }

    private void showConnectDialog() {
        JTextField hostField = new JTextField("127.0.0.1");
        JTextField portField = new JTextField("2323");
        JTextField nickField = new JTextField(nickname);

        Object[] message = {
                "Host:", hostField,
                "Port:", portField,
                "Nickname:", nickField
        };

        int option = JOptionPane.showConfirmDialog(frame, message, "Connect to Subnet", JOptionPane.OK_CANCEL_OPTION);
        if (option == JOptionPane.OK_OPTION) {
            nickname = nickField.getText().trim();
            connect(hostField.getText().trim(), Integer.parseInt(portField.getText().trim()));
        }
    }

    private void connect(String host, int port) {
        try {
            socket = new Socket(host, port);
            in = new BufferedReader(new InputStreamReader(socket.getInputStream(), "UTF-8"));
            out = new PrintWriter(new OutputStreamWriter(socket.getOutputStream(), "UTF-8"), true);

            statusLabel.setText("Connected to " + host + ":" + port);
            appendColoredText("\u001B[96m[Connected to " + host + ":" + port + "]\u001B[0m\n");

            out.println("/nick " + nickname);

            listenerThread = new Thread(this::listenToServer);
            listenerThread.start();

        } catch (Exception e) {
            JOptionPane.showMessageDialog(frame, "Connection failed: " + e.getMessage(), "Error", JOptionPane.ERROR_MESSAGE);
        }
    }

    private void sendMessage() {
        String msg = inputField.getText().trim();
        if (msg.isEmpty()) return;
        out.println(msg);
        inputField.setText("");
    }

    private void listenToServer() {
        try {
            String line;
            while ((line = in.readLine()) != null) {
                String finalLine = line;
                SwingUtilities.invokeLater(() -> appendColoredText(finalLine + "\n"));
            }
        } catch (IOException e) {
            SwingUtilities.invokeLater(() -> appendColoredText("\u001B[91m[Disconnected from server]\u001B[0m\n"));
            statusLabel.setText("Disconnected");
        }
    }

    // convierte los códigos ANSI en colores reales
    private void appendColoredText(String text) {
        StyledDocument doc = chatPane.getStyledDocument();
        Pattern pattern = Pattern.compile("\u001B\\[(\\d{1,2})m");
        Matcher matcher = pattern.matcher(text);
        int lastEnd = 0;
        Color currentColor = Color.WHITE;

        while (matcher.find()) {
            String colorCode = matcher.group(1);
            int start = matcher.start();
            appendSegment(doc, text.substring(lastEnd, start), currentColor);
            currentColor = ANSI_COLORS.getOrDefault(colorCode, currentColor);
            lastEnd = matcher.end();
        }

        appendSegment(doc, text.substring(lastEnd), currentColor);
        chatPane.setCaretPosition(doc.getLength());
    }

    private void appendSegment(StyledDocument doc, String text, Color color) {
        if (text.isEmpty()) return;
        SimpleAttributeSet attrs = new SimpleAttributeSet();
        StyleConstants.setForeground(attrs, color);
        StyleConstants.setFontFamily(attrs, "Monospaced");
        try {
            doc.insertString(doc.getLength(), text, attrs);
        } catch (BadLocationException ignored) {}
    }

    public static void main(String[] args) {
        SwingUtilities.invokeLater(SubnetClient::new);
    }
}

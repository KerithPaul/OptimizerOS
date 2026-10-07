import mysql from "mysql2/promise";

export async function query(sql: string) {
  const connection = await mysql.createConnection(
    process.env.DATABASE_URL as string,
  );
  const [rows] = await connection.query(sql);
  await connection.end();
  return rows;
}
